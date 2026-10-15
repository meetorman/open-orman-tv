"""The open conformance kit (contracts/conform.py, docs/contract/PROVIDERS.md §6): a provider on the contract passes every
check, one off it fails the right ones, and the kit's own verdict (one line per check, exit 1 on a failure).
No network: every run is offline. python3 -m unittest test_conform"""
import contextlib
import io
import logging
import unittest

import contracts
from contracts import conform

T0 = 1791936000  # 2026-10-14 00:00 UTC
logging.getLogger("orman-contracts").setLevel(logging.ERROR)  # the drops these fakes make on purpose


def work(pid, n, at=T0, **more):
    return dict({"id": "%s:w%d" % (pid, n), "kind": "movie", "title": "Film %d" % n, "year": 1950 + n,
                 "ids": {"wikidata": "Q%d" % n}, "updated_at": contracts.iso(at),
                 "art": {"poster": {"url": "https://img.example/%d.jpg" % n, "license": "cc0"}}}, **more)


class Good(contracts.Provider):
    """On the contract: metadata, availability (an offer and an airing) and content, with a real delta."""
    id, name = "good", "Good"
    types = ("metadata", "availability", "content")
    subcategories = ("movies", "channels")
    regions, trust, public, delta = ("US",), 50, True, True
    attribution = {"text": "Made up", "link": "https://good.example/about", "display": "item"}

    def __init__(self):
        self.works = {n: work(self.id, n) for n in range(1, 4)}
        self.removed = {}  # (type, id) -> when
        self.changed = T0

    def changed_at(self, contract_type):
        return self.changed

    def removed_since(self, contract_type, since):
        return [i for (t, i), at in self.removed.items() if t == contract_type and at > since]

    def change(self):
        """One addition and one removal, as the kit's delta check asks: {type: (added, removed)}."""
        self.changed = T0 + 60
        self.works[9] = work(self.id, 9, at=self.changed)
        del self.works[1]
        for t, i in (("metadata", "good:w1"), ("availability", "good:o1"), ("content", "good:c1")):
            self.removed[(t, i)] = self.changed
        return {"metadata": (["good:w9"], ["good:w1"]), "availability": (["good:o9"], ["good:o1"]),
                "content": (["good:c9"], ["good:c1"])}

    def metadata(self, query):
        channel = {"id": "good:ch", "kind": "channel", "name": "Good One", "country": "US", "updated_at": contracts.iso(T0)}
        return list(self.works.values()) + [channel]

    def availability(self, query):
        out = [{"id": "good:o%d" % n, "kind": "offer", "ids": dict(w["ids"]), "title": w["title"], "service": "good",
                "service_name": "Good", "region": "US", "access": "free", "updated_at": w["updated_at"],
                "window": {"end": None}} for n, w in self.works.items()]
        out.append({"id": "good:lineup", "kind": "channel", "name": "Good One", "channel_id": "good:ch", "service": "good",
                    "updated_at": contracts.iso(T0)})
        out.append({"id": "good:ch.%d" % (T0 + 3600), "kind": "airing", "channel_id": "good:ch", "title": "Film 2",
                    "work_id": "good:w2", "start": contracts.iso(T0 + 3600), "end": contracts.iso(T0 + 7200),
                    "updated_at": contracts.iso(T0)})
        return out

    def content(self, query):
        return [{"id": "good:c%d" % n, "kind": "movie", "title": w["title"], "service": "good", "ids": dict(w["ids"]),
                 "updated_at": w["updated_at"], "window": {"end": None}, "how": "play",
                 "streams": [{"url": "https://files.example/%d.mp4" % n, "format": "mp4"}],
                 "license": {"code": "cc0", "name": "CC0", "source": "https://files.example/%d" % n}}
                for n, w in self.works.items()]


class Bad(contracts.Provider):
    """Off the contract in several ways at once: delta undeclared, items the shape drops (a year out of range, a
    foreign id, a playable link in a synopsis), an airing without a work, a content item whose offer is another
    service's."""
    id, name = "bad", "Bad"
    types = ("metadata", "availability", "content")
    subcategories = ("movies",)
    regions, trust, public = ("US",), 50, False
    attribution = {"text": "Made up"}

    def changed_at(self, contract_type):
        return T0

    def metadata(self, query):
        return [work(self.id, 1, overview="Watch https://cdn.example/1.m3u8 now"), work(self.id, 2, year=1700),
                {"id": "other:3", "kind": "movie", "title": "Not mine", "updated_at": contracts.iso(T0)}]

    def availability(self, query):
        return [{"id": "bad:ch.%d" % (T0 + 3600), "kind": "airing", "channel_id": "bad:ch", "title": "Film 1",
                 "start": contracts.iso(T0 + 3600), "end": contracts.iso(T0 + 7200), "updated_at": contracts.iso(T0)},
                {"id": "bad:o1", "kind": "offer", "ids": {"wikidata": "Q1"}, "service": "someone", "service_name": "S",
                 "region": "US", "access": "free", "updated_at": contracts.iso(T0)}]

    def content(self, query):
        return [{"id": "bad:c1", "kind": "movie", "title": "Film 1", "service": "bad", "ids": {"wikidata": "Q1"},
                 "updated_at": contracts.iso(T0), "window": {"end": None}, "how": "play",
                 "streams": [{"url": "https://files.example/1.mp4", "format": "mp4"}],
                 "license": {"code": "cc0", "name": "CC0", "source": "https://files.example/1"}}]


class Forgetful(Good):
    """Declares delta but never names what left."""

    def removed_since(self, contract_type, since):
        return []


class LongSeries(contracts.Provider):
    """A series whose episodes outnumber a page: looking its id up matches them all (series_id)."""
    id, name = "long", "Long"
    types = ("metadata", "availability")
    subcategories = ("tv-shows",)
    regions, trust, public, delta = ("US",), 50, True, False
    attribution = {"text": "Made up", "link": "https://long.example/about", "display": "about"}
    EPISODES = contracts.MAX_LIMIT + 5

    def changed_at(self, contract_type):
        return T0

    def removed_since(self, contract_type, since):
        return None

    def metadata(self, query):
        at = contracts.iso(T0)
        episodes = [{"id": "long:e%05d" % n, "kind": "episode", "title": "Part %d" % n, "series_id": "long:show",
                     "updated_at": at} for n in range(self.EPISODES)]
        return episodes + [{"id": "long:show", "kind": "series", "title": "The Long Show", "updated_at": at},
                           {"id": "long:ch", "kind": "channel", "name": "Long One", "country": "US", "updated_at": at}]

    def availability(self, query):
        return [{"id": "long:air", "kind": "airing", "channel_id": "long:ch", "start": contracts.iso(T0),
                 "end": contracts.iso(T0 + 3600), "title": "The Long Show", "work_id": "long:show",
                 "updated_at": contracts.iso(T0)}]


def run(provider, change=None):
    checks = conform.Run(provider.id, provider, change, T0, offline=True).run()
    return {c.name: c for c in checks}


class KitTests(unittest.TestCase):
    def test_a_provider_on_the_contract_passes_every_check_but_the_proof_it_has_no_test_for(self):
        good = Good()
        checks = run(good, good.change)
        self.assertEqual(list(checks), list(conform.ORDER))
        failed = {name: c.problems for name, c in checks.items() if not c.ok}
        self.assertEqual(list(failed), ["proof"], failed)
        self.assertIn("no test_contract_good.py", failed["proof"][0])
        self.assertIn("metadata 4, availability 5, content 3", checks["full-read"].detail)
        self.assertIn("+1 -1", checks["delta"].detail)
        self.assertTrue(checks["playable"].skipped, "offline: the HEAD is skipped, not passed")
        self.assertIn("3 items pair", checks["content"].detail)
        self.assertIn("3 art with a licence", checks["licence"].detail)
        lines = [c.line("good") for c in checks.values()]
        self.assertTrue(lines[0].startswith("ok   declaration  good: metadata, availability, content, US, granted, attribution item, delta true"))
        self.assertTrue(lines[-1].startswith("FAIL proof        good: no test_contract_good.py"))

    def test_a_provider_off_the_contract_fails_the_right_checks(self):
        checks = run(Bad())
        failed = {name: "; ".join(c.problems) for name, c in checks.items() if not c.ok}
        self.assertEqual(sorted(failed), ["airings", "content", "declaration", "delta", "full-read", "proof"])
        self.assertIn("delta is required", failed["declaration"])
        self.assertIn("metadata: dropped 3", failed["full-read"], "the year, the foreign id and the playable link")
        self.assertIn("delta is not declared", failed["delta"])
        self.assertTrue(checks["playable"].ok, "the source dropped the playable link before it could be served")
        self.assertIn("1 airings have no work_id", failed["airings"])
        self.assertIn("no offer of service bad", failed["content"])
        self.assertTrue(checks["access"].ok and checks["lookups"].ok and checks["licence"].ok)

    def test_an_airing_of_a_series_with_more_episodes_than_a_page_resolves(self):
        checks = run(LongSeries())
        self.assertTrue(checks["airings"].ok, checks["airings"].problems)

    def test_a_delta_that_forgets_its_removals_fails(self):
        forgetful = Forgetful()
        checks = run(forgetful, forgetful.change)
        self.assertEqual(checks["delta"].problems, ["metadata: the removal good:w1 is not in deleted",
                                                     "availability: the removal good:o1 is not in deleted",
                                                     "content: the removal good:c1 is not in deleted"])

    def test_a_delta_declared_without_a_way_to_change_the_data_fails(self):
        self.assertIn("the rig returns no change()", "; ".join(run(Good())["delta"].problems))

    def test_main_prints_a_line_per_check_and_exits_on_a_failure(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(conform.main(["pdfilms", "--offline"]), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual([l.split()[1] for l in lines[:-1]], list(conform.ORDER))
        self.assertEqual(lines[-1], "pdfilms: passes")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(conform.main(["nosuch", "--offline"]), 1)
        self.assertIn("nosuch: fails proof", out.getvalue())
        self.assertEqual(sorted(conform.rigs()), ["pdfilms"])

    def test_providers_outside_the_package_run_by_module_path(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(conform.main(["--all", "--providers", "tests.rigged", "--offline"]), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual([l for l in lines if l.startswith(("pdfilms:", "rigged:"))], ["pdfilms: passes", "rigged: passes"])
        self.assertTrue(any(l.startswith("ok   proof        rigged: rig tests.rigged") for l in lines), lines)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(conform.main(["tests.test_conform", "--offline"]), 1)
        self.assertIn("tests.test_conform has no rig", out.getvalue())
        self.assertEqual(conform.modules(["a.b,c.d", " e.f "]), ["a.b", "c.d", "e.f"])


if __name__ == "__main__":
    unittest.main()
