"""The store (contracts/store.py, docs/contract/STORE.md): validation at ingest, tombstones, the read path's
cursor, filters, window and `since`, the fallback for a provider that isn't synced, the sync CLI, and the
budgets of a 100,000-item provider. python3 -m unittest tests.test_store"""
import contextlib
import io
import logging
import os
from pathlib import Path
import tempfile
import time
import unittest

import contracts
from contracts import store as store_module
from contracts.store import Store
from tests.test_conform import Bad, Good, T0, work

logging.getLogger("orman-contracts").setLevel(logging.ERROR)
SLACK = 3  # a budget is asserted at three times its figure: the test proves the shape of the read, not the machine


def ids(body):
    return [i["id"] for i in body["items"]]


def walk(source, path, limit, now, **params):
    """Every page of one read, following next_cursor."""
    out, cursor, pages = [], None, 0
    while True:
        query = dict({k: [v] for k, v in params.items()}, limit=[str(limit)])
        if cursor:
            query["cursor"] = [cursor]
        code, body = source.answer(path, query, {path.split("/")[2]}, now=now)
        assert code == 200, body
        out += body["items"]
        cursor, pages = body["next_cursor"], pages + 1
        if not cursor:
            return out, pages


class Windowed(Good):
    """Good plus an offer whose window closes, to see a closed window leave."""

    def __init__(self, closes_at):
        super().__init__()
        self.closes_at = closes_at

    def availability(self, query):
        out = super().availability(query)
        out.append({"id": "good:o-closing", "kind": "offer", "ids": {"wikidata": "Q7"}, "title": "Leaving", "service": "good",
                    "service_name": "Good", "region": "US", "access": "free", "updated_at": contracts.iso(T0),
                    "window": {"end": contracts.iso(self.closes_at)}})
        return out


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.good = Good()
        self.good.sync(self.store, now=T0)
        self.source = contracts.Source([self.good])

    def tearDown(self):
        self.store.close()

    def read(self, contract_type, now=T0 + 5, **params):
        return self.source.answer("/p/good/" + contract_type, {k: [v] for k, v in params.items()}, {"good"}, now=now)

    def test_a_stored_provider_serves_what_the_in_memory_path_serves_and_declares_delta(self):
        plain = contracts.Source([Good()])
        for t in ("metadata", "availability", "content"):
            params = {"from": [contracts.iso(T0)], "to": [contracts.iso(T0 + 86400)]} if t == "availability" else {}
            stored, built = self.read(t)[1], plain.answer("/p/good/" + t, params, {"good"}, now=T0 + 5)[1]
            self.assertEqual(sorted(ids(stored)), sorted(ids(built)), t)
            self.assertEqual(sorted(stored["items"], key=lambda i: i["id"]), sorted(built["items"], key=lambda i: i["id"]), t)
            self.assertEqual(stored["dropped"], 0)
            self.assertEqual(stored["sync_token"], "s_" + contracts.iso(T0))
        self.assertEqual(self.source.answer("/p", {}, {"good"})[1]["providers"][0]["delta"], True)
        self.assertEqual(plain.answer("/p", {}, {"good"})[1]["providers"][0]["delta"], True, "Good declares it itself")
        self.assertTrue(self.store.synced("good") and self.store.synced("good", "metadata") and not self.store.synced("nobody"))

    def test_ingest_validates_and_drops_so_reads_never_do(self):
        bad = Bad()
        report = bad.sync(self.store, now=T0)
        self.assertEqual((report["metadata"]["items"], report["metadata"]["dropped"]), (0, 3), "the year, the foreign id, the playable link")
        self.assertEqual(report["availability"]["items"], 2)
        code, body = contracts.Source([bad]).answer("/p/bad/metadata", {}, {"bad"}, now=T0)
        self.assertEqual((code, body["items"], body["dropped"]), (200, [], 3), "dropped reports the latest sync")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM items WHERE provider = 'bad'").fetchone()[0], 3, "2 availability, 1 content")
        bad.metadata = lambda query: [work("bad", 5)]
        self.assertEqual(bad.sync(self.store, now=T0 + 10)["metadata"], {"items": 1, "added": 1, "changed": 0, "removed": 0,
                                                                          "dropped": 0, "token": "s_" + contracts.iso(T0 + 10)})

    def test_the_cursor_is_a_keyset_and_dies_with_the_token(self):
        self.good.works.update({n: work("good", n) for n in range(10, 30)})
        self.good.sync(self.store, now=T0 + 1)
        big = self.read("metadata", limit="2000")[1]
        self.assertEqual(len(big["items"]), 24)
        self.assertIsNone(big["next_cursor"])
        small, pages = walk(self.source, "/p/good/metadata", 5, T0 + 5)
        self.assertEqual((sorted(i["id"] for i in small), pages), (sorted(ids(big)), 5))
        first = self.read("metadata", limit="5")[1]
        self.good.works[99] = work("good", 99)
        self.good.sync(self.store, now=T0 + 2)
        code, body = self.read("metadata", limit="5", cursor=first["next_cursor"])
        self.assertEqual((code, body["error"]), (410, "gone"), "the data changed while paging")
        self.assertEqual(self.read("metadata", cursor="not-a-cursor")[0], 400)

    def test_since_is_updated_at_plus_tombstones_and_an_unknown_token_is_410(self):
        token = self.read("metadata")[1]["sync_token"]
        self.assertEqual(ids(self.read("metadata", since=token)[1]), [], "nothing changed since")
        self.good.change()
        self.good.sync(self.store, now=T0 + 60)
        for t, added, removed in (("metadata", "good:w9", "good:w1"), ("availability", "good:o9", "good:o1"), ("content", "good:c9", "good:c1")):
            code, body = self.read(t, now=T0 + 65, since=token)
            self.assertEqual((code, ids(body), body["deleted"]), (200, [added], [removed]), t)
            self.assertEqual((body["sync_token"], body["next_cursor"]), ("s_" + contracts.iso(T0 + 60), None))
        self.assertEqual(self.read("metadata", since="t_not-a-token-of-this-source")[0], 410)
        self.assertEqual(self.read("metadata", since="s_" + contracts.iso(T0 - 1))[0], 410, "from before the first sync")
        self.assertEqual(self.read("metadata", since="s_" + contracts.iso(T0 + 3600))[0], 410, "from the future")
        self.assertEqual(self.read("metadata", since="s_0")[0], 410)
        # a later page of a since read carries no deleted; an id re-added loses its tombstone
        self.good.works[1] = work("good", 1)
        self.good.sync(self.store, now=T0 + 120)
        code, body = self.read("metadata", now=T0 + 125, since=token)
        self.assertEqual((sorted(ids(body)), body["deleted"]), (["good:w1", "good:w9"], []))
        # the store keeps KEEP of tombstones: after a sync that far on, a token from before them is 410 even if nothing changed
        self.assertEqual(self.read("metadata", now=T0 + store_module.KEEP + 300, since=token)[0], 200)
        self.good.sync(self.store, now=T0 + store_module.KEEP + 200)
        self.assertEqual(self.read("metadata", now=T0 + store_module.KEEP + 300, since=token)[0], 410)
        current = self.read("metadata", now=T0 + store_module.KEEP + 300)[1]["sync_token"]
        self.assertEqual(current, "s_" + contracts.iso(T0 + 120), "nothing changed: the token stayed")
        self.assertEqual(self.read("metadata", now=T0 + store_module.KEEP + 300, since=current)[1]["items"], [], "the latest token always answers")

    def test_a_sync_that_changes_nothing_keeps_the_token(self):
        token = self.read("metadata")[1]["sync_token"]
        report = self.good.sync(self.store, now=T0 + 500)
        self.assertEqual(report["metadata"], {"items": 4, "added": 0, "changed": 0, "removed": 0, "dropped": 0, "token": token})
        self.assertEqual(self.read("metadata", now=T0 + 600)[1]["sync_token"], token)
        self.good.works[2]["title"] = "Film 2, restored"
        self.good.sync(self.store, now=T0 + 500)  # the same second again: the token still moves past the last one
        code, body = self.read("metadata", now=T0 + 600, since=token)
        self.assertEqual((ids(body), body["items"][0]["title"]), (["good:w2"], "Film 2, restored"))
        self.assertGreater(body["sync_token"], token)

    def test_the_filters_are_indexed_reads(self):
        self.good.works[5] = work("good", 5, subcategories=["kids"], title="Kids Film Five")
        self.good.sync(self.store, now=T0 + 1)
        self.assertEqual(ids(self.read("metadata", kind="channel")[1]), ["good:ch"])
        self.assertEqual(sorted(ids(self.read("metadata", kind="channel,movie")[1])), ["good:ch", "good:w1", "good:w2", "good:w3", "good:w5"])
        self.assertEqual(ids(self.read("metadata", kind="nothing")[1]), [])
        self.assertEqual(ids(self.read("metadata", subcategory="kids")[1]), ["good:w5"])
        self.assertEqual(ids(self.read("metadata", subcategory="kids,news")[1]), ["good:w5"])
        self.assertEqual(ids(self.read("metadata", q="kids fi")[1]), ["good:w5"], "every word a prefix")
        self.assertEqual(ids(self.read("metadata", q="film kids")[1]), ["good:w5"], "in any order")
        self.assertEqual(ids(self.read("metadata", q="films")[1]), [], "a prefix, not a fuzzy match")
        self.assertEqual(ids(self.read("metadata", q="Good")[1]), ["good:ch"], "the channel's name")
        self.assertEqual(ids(self.read("metadata", ids="good:w2")[1]), ["good:w2"])
        self.assertEqual(ids(self.read("metadata", ids="wikidata:Q3,good:w3,imdb:none")[1]), ["good:w3"], "canonical ids, once")
        self.assertEqual(sorted(ids(self.read("availability", ids="good:ch")[1])), ["good:ch.%d" % (T0 + 3600), "good:lineup"], "channel_id, as contracts.matches")
        self.assertEqual(ids(self.read("availability", ids="good:w2", kind="airing")[1]), ["good:ch.%d" % (T0 + 3600)], "work_id")
        self.assertEqual(ids(self.read("metadata", ids="good:w2", q="film")[1]), ["good:w2"])
        self.assertEqual(ids(self.read("metadata", ids="good:w2", q="kids")[1]), [])
        self.assertEqual(ids(self.read("metadata", region="FR")[1]), [], "outside the provider's regions")
        self.assertEqual(self.read("metadata", region="France")[0], 400)

    def test_the_airing_window_comes_from_the_airings_table(self):
        at = T0 + 3600
        airing = "good:ch.%d" % at
        window = lambda start, end, **more: ids(self.read("availability", **{"from": contracts.iso(start), "to": contracts.iso(end)}, **more)[1])
        self.assertIn(airing, window(T0, T0 + 86400))
        self.assertIn(airing, window(at + 1800, at + 7200), "it overlaps the window")
        self.assertNotIn(airing, window(at + 3600, at + 7200), "it ended before the window")
        self.assertNotIn(airing, window(T0, at), "it starts at the window's end")
        self.assertEqual(window(T0, T0 + 86400, kind="airing"), [airing])
        self.assertEqual(window(T0, T0 + 86400, kind="airing", channels="good:ch"), [airing])
        self.assertEqual(window(T0, T0 + 86400, kind="airing", channels="good:other"), [])
        self.assertEqual(window(T0, T0 + 86400, kind="airing", q="film"), [airing])
        self.assertEqual(sorted(window(T0, T0 + 86400, kind="offer,channel")), ["good:lineup", "good:o1", "good:o2", "good:o3"])
        self.assertEqual(ids(self.read("availability", now=at + 3600, kind="airing")[1]), [], "the default window starts now")
        self.assertEqual(self.read("availability", **{"from": contracts.iso(T0), "to": contracts.iso(T0 + 8 * 86400)})[0], 400)

    def test_a_closed_window_is_not_served_and_leaves_at_the_next_sync(self):
        provider = Windowed(T0 + 100)
        provider.sync(self.store, now=T0)
        source = contracts.Source([provider])
        read = lambda now, **params: source.answer("/p/good/availability", {k: [v] for k, v in params.items()}, {"good"}, now=now)[1]
        self.assertIn("good:o-closing", ids(read(T0 + 50)))
        self.assertNotIn("good:o-closing", ids(read(T0 + 150)), "closed between syncs: filtered at read")
        token = read(T0 + 150)["sync_token"]
        provider.sync(self.store, now=T0 + 200)
        self.assertNotIn("good:o-closing", ids(read(T0 + 250)))
        self.assertEqual(read(T0 + 250, since=token)["deleted"], ["good:o-closing"], "a delta client is told it left")
        self.assertEqual(provider.sync(self.store, now=T0 + 300)["availability"]["removed"], 0, "never stored again")

    def test_a_provider_that_is_not_synced_is_served_as_before(self):
        class Plain(Good):
            id = "plain"

        plain = Plain()
        source = contracts.Source([plain, self.good], store=self.store)
        self.assertIsNone(plain.store)
        self.assertIs(self.good.store, self.store)
        listed = {p["id"]: p["delta"] for p in source.answer("/p", {}, {"plain", "good"})[1]["providers"]}
        self.assertEqual(listed, {"plain": True, "good": True})
        plain.delta = False
        code, body = source.answer("/p/plain/metadata", {}, {"plain"}, now=T0)
        self.assertEqual((code, len(body["items"]), body["sync_token"], body["dropped"]), (200, 3, "s_" + contracts.iso(T0), 1),
                         "built per read, as before: the hard-coded good:ch is dropped as not plain's")
        self.assertEqual(source.answer("/p/plain/metadata", {"since": [body["sync_token"]]}, {"plain"}, now=T0)[0], 410)
        self.assertEqual(source.answer("/p/-/metadata", {"q": ["film 2"]}, {"plain", "good"}, now=T0)[1]["providers"], ["plain", "good"])

    def test_the_sync_command_writes_a_file_the_server_can_attach(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "source.db")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(store_module.main(["sync", "pdfilms", "--db", path]), 0)
            lines = out.getvalue().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(lines[0].startswith("pdfilms availability: 12 items (+12 ~0 -0), dropped 0, token s_"), lines[0])
            self.assertTrue(Path(path).is_file())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(store_module.main(["sync", "nosuch", "--db", path]), 1)
            from contracts.pdfilms import PublicDomainFilms
            with Store(path) as opened:
                self.assertEqual(opened.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                source = contracts.Source([PublicDomainFilms()], store=opened)
                code, body = source.answer("/p/pdfilms/content", {"q": ["detour"]}, {"pdfilms"})
                self.assertEqual((code, ids(body)), (200, ["pdfilms:Q2062566"]))
                self.assertEqual(source.answer("/p", {}, {"pdfilms"})[1]["providers"][0]["delta"], True)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(store_module.main([]), 2)


N_ITEMS, N_AIRINGS, N_CHANNELS = 100000, 50000, 50
SUBS = ("movies", "tv-shows", "news", "kids", "documentaries", "sports")


class Big(contracts.Provider):
    """A synthetic provider the size the store is for: 100,000 works, 50 channels, 50,000 airings."""
    id, name = "big", "Big"
    types = ("metadata", "availability")
    subcategories = SUBS
    regions, trust, public, delta = ("US",), 50, True, False
    attribution = {"text": "Made up"}
    revision = 0

    def metadata(self, query):
        out = []
        for n in range(N_ITEMS):
            out.append({"id": "big:w%d" % n, "kind": "movie", "title": "Film %d %s" % (n, ("alpha", "beta", "gamma", "delta")[n % 4]),
                        "year": 1900 + n % 120, "ids": {"wikidata": "Q%d" % n, "imdb": "tt%07d" % n},
                        "updated_at": contracts.iso(T0 + n % 86400), "subcategories": [SUBS[n % len(SUBS)]] + (["rare"] if n % 1000 == 0 else []),
                        "overview": "An overview of film %d, long enough to look like one." % n})
            if self.revision and n % 100 == 0:  # one in a hundred changes per revision
                out[-1]["title"] += " (revision %d)" % self.revision
        for c in range(N_CHANNELS):
            out.append({"id": "big:ch%d" % c, "kind": "channel", "name": "Channel %d" % c, "country": "US", "updated_at": contracts.iso(T0)})
        return out

    def availability(self, query):
        out, per = [], N_AIRINGS // N_CHANNELS
        for c in range(N_CHANNELS):
            for k in range(per):
                start = T0 + k * 1800
                out.append({"id": "big:ch%d.%d" % (c, start), "kind": "airing", "channel_id": "big:ch%d" % c, "channel_name": "Channel %d" % c,
                            "title": "Show %d" % (k % 500), "work_id": "big:w%d" % (k % 1000), "start": contracts.iso(start),
                            "end": contracts.iso(start + 1800), "updated_at": contracts.iso(T0)})
        return out


class BudgetTests(unittest.TestCase):
    """The issue's budgets on a file-backed store: any page under 50 ms, a since read under 20 ms, a 4 h x 50 channel
    window under 20 ms. Each is asserted at SLACK times the figure, best of three reads."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="orman-store-")
        cls.store = Store(os.path.join(cls.directory.name, "big.db"))
        cls.big = Big()
        cls.big.sync(cls.store, now=T0)
        cls.source = contracts.Source([cls.big])
        cls.taken = {}

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.directory.cleanup()
        print("\n  budgets (best of 3): " + ", ".join("%s %.1f ms" % (k, v * 1000) for k, v in cls.taken.items()))

    def read(self, label, budget, path="/p/big/metadata", now=T0 + 10, **params):
        best, body = None, None
        for _ in range(3):
            started = time.perf_counter()
            code, body = self.source.answer(path, {k: [v] for k, v in params.items()}, {"big"}, now=now)
            took = time.perf_counter() - started
            best = took if best is None else min(best, took)
        self.assertEqual(code, 200, body)
        self.taken[label] = best
        self.assertLess(best, budget * SLACK, "%s took %.1f ms, budget %d ms" % (label, best * 1000, budget * 1000))
        return body

    def test_the_sync_held_everything(self):
        state = self.store.state("big", "metadata")
        self.assertEqual((state["count"], state["dropped"]), (N_ITEMS + N_CHANNELS, 0))
        self.assertEqual(self.store.state("big", "availability")["count"], N_AIRINGS)

    def test_any_page_is_under_50_ms(self):
        page = self.read("page 1", 0.05)
        self.assertEqual(len(page["items"]), 500)
        self.read("page 2", 0.05, cursor=page["next_cursor"])
        page = self.read("page of 2000", 0.05, limit="2000")
        for _ in range(25):  # halfway in
            page = self.source.answer("/p/big/metadata", {"limit": ["2000"], "cursor": [page["next_cursor"]]}, {"big"}, now=T0 + 10)[1]
        self.read("page 27 of 2000", 0.05, limit="2000", cursor=page["next_cursor"])
        self.assertEqual(len(self.read("kind", 0.05, kind="channel")["items"]), N_CHANNELS)
        self.assertEqual(len(self.read("two kinds", 0.05, kind="movie,channel")["items"]), 500)
        self.assertEqual(len(self.read("subcategory", 0.05, subcategory="movies")["items"]), 500)
        self.assertEqual(len(self.read("rare subcategory", 0.05, subcategory="rare")["items"]), N_ITEMS // 1000)
        self.assertEqual(len(self.read("q", 0.05, q="film 1234")["items"]), 11, "1234 and 12340..12349")
        self.assertEqual(len(self.read("q common", 0.05, q="alpha")["items"]), 500)
        wanted = ",".join("wikidata:Q%d" % n for n in range(0, 50000, 100))
        self.assertEqual(len(self.read("ids 500", 0.05, ids=wanted)["items"]), 500)
        self.read("availability page 1", 0.05, "/p/big/availability")
        self.read("7 day window", 0.05, "/p/big/availability", limit="2000", **{"from": contracts.iso(T0), "to": contracts.iso(T0 + 7 * 86400)})

    def test_a_window_of_4_hours_and_50_channels_is_under_20_ms(self):
        channels = ",".join("big:ch%d" % c for c in range(N_CHANNELS))
        start = T0 + 86400
        body = self.read("4 h x 50 channels", 0.02, "/p/big/availability", channels=channels,
                         **{"from": contracts.iso(start), "to": contracts.iso(start + 4 * 3600)})
        self.assertEqual(len(body["items"]), N_CHANNELS * 8)
        self.assertTrue(all(i["kind"] == "airing" and contracts.parse_time(i["end"]) > start for i in body["items"]))

    def test_z_a_since_read_is_under_20_ms(self):  # last: it changes the data
        token = self.source.answer("/p/big/metadata", {"limit": ["1"]}, {"big"}, now=T0 + 10)[1]["sync_token"]
        self.big.revision = 1
        report = self.big.sync(self.store, now=T0 + 100)
        self.assertEqual(report["metadata"]["changed"], N_ITEMS // 100)
        body = self.read("since", 0.02, now=T0 + 110, since=token)
        self.assertEqual((len(body["items"]), body["next_cursor"] is not None), (500, True))
        self.assertTrue(all("(revision 1)" in i["title"] for i in body["items"]))
        self.read("since, availability", 0.02, "/p/big/availability", now=T0 + 110, since=token)


if __name__ == "__main__":
    unittest.main()
