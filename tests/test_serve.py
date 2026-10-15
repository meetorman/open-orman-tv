"""The reference source (contracts/serve.py) over HTTP: the open handshake, the view a token picks, the 404 for a
provider the caller doesn't hold, the envelope exactly as contracts.Source answers it, the conformance kit
reading the running server (conform --url), ETag and 304, gzip, and a source served from the store. No network
beyond loopback. python3 -m unittest test_serve"""
import contextlib
import gzip
import io
import json
import logging
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import contracts
from contracts import conform, serve
from contracts.pdfilms import PublicDomainFilms
from contracts.store import Store

logging.getLogger("orman-contracts").setLevel(logging.ERROR)
logging.getLogger("orman-source").setLevel(logging.ERROR)
ALICE, BOB = "alice-holds-secret", "bob-holds-nothing"
CONFIG = {"id": "test-source", "name": "Test source", "providers": ["pdfilms"],
          "members": [{"name": "Alice", "token": ALICE, "providers": ["secret"]},
                      {"name": "Bob", "token": BOB, "providers": []}]}


class Secret(contracts.Provider):
    """A private provider: only a member granted it sees it."""
    id, name = "secret", "Secret"
    types = ("metadata",)
    subcategories = ("movies",)
    regions, trust, public, delta = ("US",), 50, False, False
    attribution = {"text": "Made up"}

    def changed_at(self, contract_type):
        return 1791936000

    def metadata(self, query):
        return [{"id": "secret:w1", "kind": "movie", "title": "Hidden Film", "year": 1950, "ids": {"wikidata": "Q1"},
                 "updated_at": "2026-10-14T00:00:00Z"}]


class ServeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = serve.App(CONFIG, [PublicDomainFilms(), Secret()])
        cls.server = serve.listen(cls.app, "127.0.0.1", 0)
        cls.url = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path, token=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        try:
            with urlopen(Request(self.url + path, headers=headers), timeout=10) as r:
                return r.status, json.load(r)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def raw(self, path, **headers):
        """(status, headers, bytes) of one GET, the body as sent."""
        try:
            with urlopen(Request(self.url + path, headers=headers), timeout=10) as r:
                return r.status, r.headers, r.read()
        except HTTPError as error:
            with error:
                return error.code, error.headers, error.read()

    def test_the_handshake_is_open_and_names_the_public_providers(self):
        code, body = self.get("/v1/source")
        self.assertEqual(code, 200)
        self.assertEqual((body["id"], body["name"]), ("test-source", "Test source"))
        self.assertEqual(sorted(body["contracts"]), ["availability", "content", "metadata"])
        self.assertEqual([(p["id"], p["access"]) for p in body["providers"]], [("pdfilms", "public")])

    def test_a_token_picks_the_view_and_no_token_is_the_public_providers(self):
        listed = lambda token: [p["id"] for p in self.get("/v1/p", token)[1]["providers"]]
        self.assertEqual(listed(None), ["pdfilms"])
        self.assertEqual(listed(BOB), ["pdfilms"])
        self.assertEqual(listed(ALICE), ["pdfilms", "secret"])
        self.assertEqual(self.get("/v1/p", "nobody")[0], 401)

    def test_an_ungranted_provider_is_404_and_a_granted_one_answers(self):
        self.assertEqual(self.get("/v1/p/secret/metadata")[0], 404)
        self.assertEqual(self.get("/v1/p/secret/metadata", BOB)[0], 404)
        code, body = self.get("/v1/p/secret/metadata", ALICE)
        self.assertEqual((code, [i["id"] for i in body["items"]]), (200, ["secret:w1"]))
        self.assertEqual(self.get("/v1/nothing")[0], 404)
        self.assertEqual(self.get("/v1/p/pdfilms/content?limit=0")[0], 400)

    def test_the_envelope_over_http_is_the_one_source_produces(self):
        for token in (None, ALICE):
            code, body = self.get("/v1/p/pdfilms/content?kind=movie&limit=5", token)
            self.assertEqual(code, 200)
            expected = self.app.source.answer("/p/pdfilms/content", {"kind": ["movie"], "limit": ["5"]}, {"pdfilms"})[1]
            body.pop("generated_at"), expected.pop("generated_at")
            self.assertEqual(body, expected)
            self.assertEqual(len(body["items"]), 5)
            self.assertTrue(body["next_cursor"])
        code, body = self.get("/v1/p/-/content?q=detour")
        self.assertEqual((code, [i["id"] for i in body["items"]]), (200, ["pdfilms:Q2062566"]))

    def test_the_kit_passes_over_http(self):
        checks = conform.run_url("pdfilms", self.url, offline=True)
        self.assertEqual([c.name for c in checks], list(conform.ORDER))
        failed = [c.line("pdfilms") for c in checks if not c.ok]
        self.assertEqual(failed, [], "\n" + "\n".join(failed))
        by_name = {c.name: c for c in checks}
        self.assertTrue(by_name["access"].skipped, "public: no ungranted view without --other-token")
        self.assertEqual(by_name["declaration"].detail.split(", ")[:2], ["availability", "content"])
        # a private provider with both tokens: the ungranted half of access runs for real
        checks = {c.name: c for c in conform.run_url("secret", self.url, token=ALICE, other=BOB, offline=True)}
        failed = {name: c.problems for name, c in checks.items() if not c.ok}
        self.assertEqual(list(failed), ["proof"], failed)  # a test provider has no test_contract_secret.py
        self.assertFalse(checks["access"].skipped)
        self.assertEqual(conform.run_url("secret", self.url, offline=True)[0].problems,
                         ["%s/v1/p does not list secret for this view (no token)" % self.url])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(conform.main(["pdfilms", "--url", self.url, "--offline"]), 0)
        self.assertEqual(out.getvalue().splitlines()[-1], "pdfilms: passes")

    def test_an_envelope_carries_an_etag_and_answers_304_to_it(self):
        code, headers, body = self.raw("/v1/p/pdfilms/content?limit=5&kind=movie")
        tag = headers["ETag"]
        self.assertEqual(code, 200)
        self.assertTrue(tag.startswith('W/"s_'), tag)
        self.assertEqual(headers["Vary"], "Accept-Encoding")
        self.assertEqual(self.raw("/v1/p/pdfilms/content?kind=movie&limit=5")[1]["ETag"], tag, "the canonical query: order doesn't matter")
        self.assertEqual(self.raw("/v1/p/pdfilms/content?kind=movie&limit=5", Authorization="Bearer " + ALICE)[1]["ETag"], tag, "nor the member")
        self.assertNotEqual(self.raw("/v1/p/pdfilms/content?limit=6&kind=movie")[1]["ETag"], tag)
        self.assertNotEqual(self.raw("/v1/p/pdfilms/availability")[1]["ETag"], tag)
        code, headers, body = self.raw("/v1/p/pdfilms/content?limit=5&kind=movie", **{"If-None-Match": tag})
        self.assertEqual((code, headers["ETag"], body), (304, tag, b""))
        self.assertEqual(self.raw("/v1/p/pdfilms/content?limit=5&kind=movie", **{"If-None-Match": '"other", ' + tag})[0], 304)
        self.assertEqual(self.raw("/v1/p/pdfilms/content?limit=5&kind=movie", **{"If-None-Match": '"other"'})[0], 200)
        self.assertEqual(self.raw("/v1/p/pdfilms/content?limit=6&kind=movie", **{"If-None-Match": tag})[0], 200)
        for path in ("/v1/source", "/v1/p", "/v1/p/-/content?q=detour", "/v1/p/secret/metadata"):
            self.assertNotIn("ETag", self.raw(path)[1], path)
        self.assertTrue(serve.none_match("*", tag) and serve.none_match(tag[2:], tag) and not serve.none_match("", tag))

    def test_json_is_gzipped_when_the_client_allows_it(self):
        code, headers, body = self.raw("/v1/p/pdfilms/content", **{"Accept-Encoding": "gzip, deflate"})
        self.assertEqual((code, headers["Content-Encoding"]), (200, "gzip"))
        self.assertEqual(json.loads(gzip.decompress(body))["provider"], "pdfilms")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        code, headers, plain = self.raw("/v1/p/pdfilms/content")
        self.assertIsNone(headers["Content-Encoding"])
        self.assertEqual(json.loads(plain), json.loads(gzip.decompress(body)) | {"generated_at": json.loads(plain)["generated_at"]})
        self.assertGreater(len(plain), len(body))
        self.assertIsNone(self.raw("/v1/p/pdfilms/content?limit=1", **{"Accept-Encoding": "gzip"})[1]["Content-Encoding"], "a small body as it is")
        self.assertIsNone(self.raw("/v1/p/pdfilms/content", **{"Accept-Encoding": "gzip;q=0, identity"})[1]["Content-Encoding"])
        self.assertIsNone(self.raw("/v1/p/pdfilms/content", **{"Accept-Encoding": "br"})[1]["Content-Encoding"])
        self.assertTrue(serve.accepts_gzip("*") and serve.accepts_gzip("GZIP;q=0.5") and not serve.accepts_gzip(None))

    def test_a_source_with_a_store_serves_from_it(self):
        store = Store(":memory:")
        films, secret = PublicDomainFilms(), Secret()
        films.sync(store, now=1791936000)
        app = serve.App(CONFIG, [films, secret], store)
        server = serve.listen(app, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = "http://127.0.0.1:%d" % server.server_address[1]
            with urlopen(url + "/v1/p", timeout=10) as r:
                self.assertEqual([(p["id"], p["delta"]) for p in json.load(r)["providers"]], [("pdfilms", True)])
            with urlopen(url + "/v1/p/pdfilms/content?q=detour", timeout=10) as r:
                body = json.load(r)
                self.assertEqual(([i["id"] for i in body["items"]], r.headers["ETag"].startswith('W/"s_2026-10-14T00:00:00Z.')),
                                 (["pdfilms:Q2062566"], True))
            with urlopen(url + "/v1/p/pdfilms/content?since=" + body["sync_token"], timeout=10) as r:
                self.assertEqual(json.load(r)["items"], [])
            self.assertIs(films.store, store)
            self.assertIsNone(secret.store, "not synced: served as before")
            with urlopen(Request(url + "/v1/p/secret/metadata", headers={"Authorization": "Bearer " + ALICE}), timeout=10) as r:
                self.assertEqual([i["id"] for i in json.load(r)["items"]], ["secret:w1"])
            checks = {c.name: c for c in conform.run_url("pdfilms", url, offline=True)}
            self.assertEqual([n for n, c in checks.items() if not c.ok], [])
            self.assertTrue(checks["declaration"].detail.endswith("delta true"), checks["declaration"].detail)
        finally:
            server.shutdown()
            server.server_close()
            store.close()

    def test_a_provider_is_loaded_by_id_or_by_module_path(self):
        self.assertEqual(serve.load("pdfilms").id, "pdfilms")
        self.assertEqual(serve.load("tests.rigged").id, "rigged")
        self.assertEqual(serve.load("tests.test_serve").id, "secret")  # this module's own, not the ones it imports
        with self.assertRaises(LookupError):
            serve.load("tests.test_conform")  # several of its own
        with self.assertRaises(LookupError):
            serve.load("tests.test_schema")  # none
        with self.assertRaises(ImportError):
            serve.load("nosuch")

    def test_the_default_config_serves_pdfilms_public(self):
        config = serve.read_config("no-such-source.json")
        self.assertEqual(config["providers"], ["pdfilms"])
        self.assertEqual(serve.load("pdfilms").id, "pdfilms")
        with self.assertRaises(LookupError):
            serve.load("conform")


if __name__ == "__main__":
    unittest.main()
