"""The reference source (contracts/serve.py) over HTTP: the open handshake, the view a token picks, the 404 for a
provider the caller doesn't hold, the envelope exactly as contracts.Source answers it, and the conformance kit
reading the running server (conform --url). No network beyond loopback. python3 -m unittest test_serve"""
import contextlib
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

    def test_the_default_config_serves_pdfilms_public(self):
        config = serve.read_config("no-such-source.json")
        self.assertEqual(config["providers"], ["pdfilms"])
        self.assertEqual(serve.load("pdfilms").id, "pdfilms")
        with self.assertRaises(LookupError):
            serve.load("conform")


if __name__ == "__main__":
    unittest.main()
