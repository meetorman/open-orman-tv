"""The reference source: your providers over HTTP (docs/guides/run-your-own-source.md).

    python3 -m contracts.serve                              pdfilms, public, on port 8080
    python3 -m contracts.serve --port 8080 --config source.json

`source.json` (see source.example.json) names the source, the providers to load (`contracts/<id>.py`, each a
`contracts.Provider` subclass built with no arguments) and the members: a bearer token each and the provider ids
it holds. `Authorization: Bearer <token>` picks that member's view; no token sees the public providers only; a
token the file doesn't name is 401. A public provider is in every view. The routes (docs/contract/ENVELOPES.md):

  GET /v1/source                   open: the source's id and name, the contracts versions, the public providers
  GET /v1/p                        the providers in the caller's view
  GET /v1/p/{provider}/{type}      the envelope, exactly as contracts.Source answers it; 404 when not in the view
  GET /v1/p/-/{type}?q=|ids=       a lookup across the view

Stdlib only. No TLS, no sessions, no rate limit: keep it on your LAN or put a reverse proxy in front of it.
"""
import argparse
import hmac
import importlib
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from urllib.parse import parse_qs, unquote, urlsplit

import contracts

LOG = logging.getLogger("orman-source")
DEFAULT = {"id": "my-source", "name": "My source", "providers": ["pdfilms"], "members": []}


def read_config(path):
    """The config at `path`; when it isn't there, DEFAULT (pdfilms, public, no members)."""
    path = Path(path)
    if not path.is_file():
        LOG.info("no %s: serving %s (copy source.example.json to add members)", path, ", ".join(DEFAULT["providers"]))
        return dict(DEFAULT)
    with path.open() as f:
        config = json.load(f)
    for key in ("id", "name", "providers"):
        if not config.get(key):
            raise SystemExit("%s: %r is missing" % (path, key))
    for member in config.get("members") or []:
        if not member.get("token"):
            raise SystemExit("%s: a member without a token" % path)
    return config


def load(pid):
    """The provider contracts/<pid>.py declares: its contracts.Provider subclass with that id, built with no arguments."""
    module = importlib.import_module("contracts." + pid)
    for value in vars(module).values():
        if isinstance(value, type) and issubclass(value, contracts.Provider) and value is not contracts.Provider \
                and value.id == pid:
            return value()
    raise LookupError("contracts/%s.py declares no contracts.Provider with id %r" % (pid, pid))


class App:
    """One source: its providers on a contracts.Source, and who sees which."""

    def __init__(self, config, providers):
        self.id, self.name = config["id"], config["name"]
        self.source = contracts.Source(providers)
        self.public = {p.id for p in providers if p.public}
        self.members = {m["token"]: self.public | set(m.get("providers") or ()) for m in config.get("members") or []}

    def view(self, authorization):
        """The provider ids the caller sees; None for a token the config doesn't name."""
        if not authorization:
            return set(self.public)
        scheme, _, token = authorization.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            return None
        for known, held in self.members.items():
            if hmac.compare_digest(known, token):
                return set(held)
        return None

    def answer(self, raw_path, authorization=None):
        """(status, body) for one GET: the path with its query string, and the Authorization header."""
        parts = urlsplit(raw_path)
        path = unquote(parts.path)
        if path == "/v1/source":
            return 200, {"id": self.id, "name": self.name, "contracts": self.source.contracts(),
                         "providers": [p.describe() for p in self.source.providers.values() if p.id in self.public]}
        if path != "/v1/p" and not path.startswith("/v1/p/"):
            return 404, {"error": "not_found"}
        seen = self.view(authorization)
        if seen is None:
            return 401, {"error": "unauthorized", "message": "This source doesn't know that token."}
        return self.source.answer(path[3:], parse_qs(parts.query, keep_blank_values=True), seen)


class Handler(BaseHTTPRequestHandler):
    server_version, sys_version = "open-orman-tv", ""

    def do_GET(self):
        code, body = self.server.app.answer(self.path, self.headers.get("Authorization"))
        raw = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if code == 401:
            self.send_header("WWW-Authenticate", "Bearer")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        LOG.info("%s %s", self.address_string(), fmt % args)


def listen(app, host, port):
    """A server bound to host:port (port 0: any free one), not yet serving: call serve_forever()."""
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.app = app
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m contracts.serve", description="Serve your providers over HTTP.")
    parser.add_argument("--config", default="source.json", help="the source, its providers and members (default source.json)")
    parser.add_argument("--host", default="0.0.0.0", help="the address to bind (default every interface)")
    parser.add_argument("--port", type=int, default=8080, help="the port (default 8080)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    config = read_config(args.config)
    app = App(config, [load(pid) for pid in config["providers"]])
    server = listen(app, args.host, args.port)
    LOG.info("%s (%s) on http://%s:%d/v1/source: %s", app.id, app.name, *server.server_address[:2],
             ", ".join("%s (%s)" % (pid, "public" if pid in app.public else "private") for pid in app.source.providers))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
