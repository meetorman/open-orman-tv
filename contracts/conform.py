"""The open conformance kit (docs/contract/CONFORMANCE.md): is this provider on the contract?

    python3 -m contracts.conform freemovies             one provider, one line per check, exit 1 on any failure
    python3 -m contracts.conform freetv pdfilms          several
    python3 -m contracts.conform --all                   every provider with a rig (contracts/<id>.py: `rig`)
    python3 -m contracts.conform pdfilms --offline       no network: the HEAD of each stream URL is skipped
    python3 -m contracts.conform pdfilms --url http://127.0.0.1:8080 [--token T] [--other-token T]
                                                         the same checks read over HTTP from a running source

A provider is run in-process by default: `contracts.<id>.rig(directory, now)` builds it from its fixture
(`contracts/fixtures/<id>.json`, or the provider's own data) in a scratch directory and returns the provider, or
`(provider, change)` where `change()` makes one addition and one removal and returns `{type: (added ids, removed
ids)}` so the delta check knows what to expect. The kit puts the provider alone on a `contracts.Source`, reads it as
a member whose view holds it and as one whose view doesn't, and runs the ten checks of the definition of done:

  1 declaration  the /v1/p entry validates (common.schema.json#/$defs/declaration): id, known types, ISO regions,
                 attribution, `delta` set
  2 full-read    every declared type read whole: `dropped` 0, every id prefixed, `next_cursor` terminates, every
                 page inside the budget, a small-limit walk is the same id set as one big read
  3 delta        `delta: true`: a `since` read after `change()` returns the change and its removal; `false`: 410;
                 a foreign token: 410 either way
  4 playable     nothing playable outside `x-playable` keys; each stream URL is https and answers a HEAD
  5 airings      every airing's `work_id` resolves in the same provider's metadata
  6 content      every content item pairs with an offer of the same `service` and with a work, by strong id or
                 `title_id`
  7 licence      every art has `license`; a `display: item` provider credits with text and link
  8 lookups      `ids=` (own id, canonical id) and `q=` (a title word) each return the item, on the provider's
                 route and across the view
  9 access       ungranted: absent from /v1/p, 404 on every route and on /v1/play; granted: listed and 200
 10 proof        the rig, and tests/test_contract_<id>.py

The only network is the HEAD of check 4 (`--offline` skips it). The same checks run from the tests
(test_contract_<id>.py: `conform.run(id, offline=True)`), so a provider that passes here passes there.

With `--url` the kit reads a running source (contracts/serve.py) instead: the provider is what the server declares,
`--token` is a view that holds it, `--other-token` one that doesn't (default: no token, the public providers). What
needs the rig is skipped and says so: a `delta: true` change, and the ungranted half of access for a public provider
without `--other-token`.
"""
import argparse
import importlib
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import contracts
from contracts import schema

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
ORDER = ("declaration", "full-read", "delta", "playable", "airings", "content", "licence", "lookups", "access", "proof")
STRONG = ("imdb", "wikidata", "tmdb", "eidr")  # the ids that pair a content item with a work anywhere (§2.8)
WEEK = 7 * 86400
SMALL = 2  # the small page of the walk
FOREIGN = "t_not-a-token-of-this-source"
OTHER = frozenset({"someone-else"})  # a view that doesn't hold the provider
HEAD_TIMEOUT = 20


class Check:
    def __init__(self, name, problems, detail, skipped=False):
        self.name, self.problems, self.detail, self.skipped = name, list(problems), detail, skipped

    @property
    def ok(self):
        return not self.problems

    def line(self, pid):
        state = "skip" if self.skipped else "ok" if self.ok else "FAIL"
        text = self.detail if self.ok else "; ".join(self.problems[:4]) + (
            " (+%d more)" % (len(self.problems) - 4) if len(self.problems) > 4 else "")
        return "%-4s %-12s %s: %s" % (state, self.name, pid, text)


def rigs():
    """The provider ids with a rig: every contracts/<id>.py whose module defines `rig`."""
    found = []
    for path in sorted(HERE.glob("*.py")):
        name = path.stem
        if name.startswith("_") or name in ("schema", "conform", "identity"):
            continue
        if re.search(r"^def rig\(", path.read_text(), re.M):
            found.append(name)
    return found


def build(pid, directory, now):
    """(provider, change) from contracts.<pid>.rig; change is None when the rig can't make one."""
    module = importlib.import_module("contracts." + pid)
    rig = getattr(module, "rig", None)
    if rig is None:
        raise LookupError("contracts/%s.py has no rig(directory, now): nothing to run the kit against" % pid)
    made = rig(directory, now)
    if isinstance(made, tuple):
        provider, change = made
    else:
        provider, change = made, None
    if provider.id != pid:
        raise LookupError("contracts.%s.rig built provider %r" % (pid, provider.id))
    return provider, change


def head(url):
    """The status a HEAD without headers gets, or the error as text."""
    try:
        with urlopen(Request(url, method="HEAD"), timeout=HEAD_TIMEOUT) as r:
            return r.status
    except HTTPError as error:
        return error.code
    except (URLError, OSError, ValueError) as error:
        return str(error)


def fetch(url, token=None, timeout=HEAD_TIMEOUT):
    """(status, body) of one GET on a running source; a body that isn't JSON is {"error": "not_json"}."""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urlopen(Request(url, headers=headers), timeout=timeout) as r:
            return r.status, json.load(r)
    except HTTPError as error:
        with error:
            try:
                return error.code, json.load(error)
            except ValueError:
                return error.code, {"error": "not_json"}


class Remote:
    """Reads a running source over HTTP: a view that holds the provider is `token`, one that doesn't is `other`."""

    def __init__(self, url, pid, token=None, other=None):
        self.url, self.pid, self.token, self.other = url.rstrip("/"), pid, token, other

    def __call__(self, path, params, seen, now):
        query = urlencode({k: v[0] for k, v in params.items()})
        return fetch("%s/v1%s%s" % (self.url, path, "?" + query if query else ""),
                     self.token if self.pid in seen else self.other)


def declared(remote):
    """A stand-in provider from what the server declares: its /v1/p entry for the view that holds it, and whether
    /v1/source lists it as public."""
    code, body = remote("/p", {}, {remote.pid}, None)
    entry = next((p for p in body.get("providers") or [] if p.get("id") == remote.pid), None) if code == 200 else None
    if entry is None:
        raise LookupError("%s/v1/p does not list %s for this view (%s)" % (remote.url, remote.pid, "--token" if remote.token else "no token"))
    code, body = fetch(remote.url + "/v1/source")
    provider = contracts.Provider()
    for key in ("id", "name", "types", "subcategories", "regions", "trust", "attribution", "notice", "delta"):
        if key in entry:
            setattr(provider, key, tuple(entry[key]) if isinstance(entry[key], list) else entry[key])
    provider.public = code == 200 and any(p.get("id") == remote.pid for p in body.get("providers") or [])
    return provider


def arts(value, path="item", under=False):
    """Every art object in value (a dict with `url` that isn't a stream), as (path, dict)."""
    found = []
    if isinstance(value, dict):
        if "url" in value and "format" not in value and path.split(".")[-1].rstrip("0123456789[]") != "streams":
            return [(path, value)]
        for k, v in value.items():
            if k == "streams":
                continue
            found += arts(v, path + "." + k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found += arts(v, "%s[%d]" % (path, i))
    return found


def strong_ids(item):
    return {(k, v) for k, v in (item.get("ids") or {}).items() if k in STRONG}


class Run:
    """One provider under the kit: a Source holding it alone, read with a view that has it and one that doesn't."""

    def __init__(self, pid, provider, change, now, offline=False, budget=5.0, remote=None):
        self.pid, self.provider, self.change = pid, provider, change
        self.now, self.offline, self.budget = int(now), offline, budget
        self.remote = remote  # a Remote: read a running source instead of the Source below
        self.source = contracts.Source([provider]) if remote is None else None
        self.read = remote or (lambda path, params, seen, now: self.source.answer(path, params, seen, now=now))
        self.view = {pid}
        self.items, self.tokens, self.slowest = {}, {}, {}
        self.feed_types = [t for t in provider.types if t not in contracts.MEMBER_TYPES]
        self.checks = {}

    def params(self, contract_type, **params):
        if contract_type == "availability":  # the whole airing window the contract allows
            params.setdefault("from", contracts.iso(self.now))
            params.setdefault("to", contracts.iso(self.now + WEEK))
        return {k: [str(v)] for k, v in params.items() if v is not None and v != ""}

    def get(self, path, seen=None, **params):
        contract_type = path.rsplit("/", 1)[-1]
        started = time.monotonic()
        code, body = self.read(path, self.params(contract_type, **params), set(self.view if seen is None else seen), self.now)
        took = time.monotonic() - started
        self.slowest[contract_type] = max(self.slowest.get(contract_type, 0), took)
        return code, body

    def walk(self, contract_type, limit):
        """(items, problems): every page of one type, following next_cursor until it ends."""
        items, problems, cursor, pages = [], [], None, 0
        while True:
            code, body = self.get("/p/%s/%s" % (self.pid, contract_type), limit=limit, cursor=cursor)
            if code != 200:
                problems.append("%s: page %d answered %s %s" % (contract_type, pages + 1, code, body.get("message", "")))
                break
            pages += 1
            errors = []
            schema.check(body, {"$ref": "common.schema.json#/$defs/envelope"}, "common.schema.json", "envelope", errors)
            problems += ["%s: %s" % (contract_type, e) for e in errors[:3]]
            if body["dropped"]:
                problems.append("%s: dropped %d (the journal says what was wrong)" % (contract_type, body["dropped"]))
            bad = [i["id"] for i in body["items"] if not str(i.get("id", "")).startswith(self.pid + ":")]
            if bad:
                problems.append("%s: %d ids without the prefix %s: (%s)" % (contract_type, len(bad), self.pid, bad[0]))
            if cursor is None:
                self.tokens[contract_type] = body["sync_token"]
            items += body["items"]
            cursor = body["next_cursor"]
            if not cursor:
                break
            if pages > len(items) // limit + 2:
                problems.append("%s: next_cursor does not terminate (%d pages for %d items)" % (contract_type, pages, len(items)))
                break
        return items, problems

    # the checks: each returns a Check

    def check_declaration(self):
        code, body = self.get("/p")
        entry = next((p for p in body["providers"] if p.get("id") == self.pid), None)
        if entry is None:
            return Check("declaration", ["not listed in /v1/p for a view that holds it"], "")
        errors = []
        schema.check(entry, {"$ref": "common.schema.json#/$defs/declaration"}, "common.schema.json", "declaration", errors)
        problems = [e.replace("declaration.", "") for e in errors]
        unknown = [t for t in entry.get("types") or [] if t not in schema.types()]
        if unknown:
            problems.append("types: %s have no <type>.schema.json" % ", ".join(unknown))
        if "delta" not in entry:
            problems.append("delta is not declared (contracts.Provider.delta = True|False)")
        if not self.feed_types:
            problems.append("no feed type declared: nothing to read")
        display = (entry.get("attribution") or {}).get("display", "about")
        return Check("declaration", problems, "%s, %s, %s, attribution %s, delta %s" % (
            ", ".join(entry.get("types") or []), "/".join(entry.get("regions") or []), entry.get("access"), display,
            str(entry.get("delta")).lower()))

    def check_full_read(self):
        problems, counts = [], []
        for t in self.feed_types:
            big, found = self.walk(t, contracts.MAX_LIMIT)
            problems += found
            ids = [i["id"] for i in big]
            if len(set(ids)) != len(ids):
                problems.append("%s: %d ids repeat in one read" % (t, len(ids) - len(set(ids))))
            small, found = self.walk(t, SMALL)
            problems += found
            if {i["id"] for i in small} != set(ids):
                problems.append("%s: a walk with limit=%d gives %d ids, one read gives %d" % (t, SMALL, len(small), len(ids)))
            if self.slowest.get(t, 0) > self.budget:
                problems.append("%s: a page took %.1f s, over the %.1f s budget" % (t, self.slowest[t], self.budget))
            self.items[t] = big
            counts.append("%s %d" % (t, len(big)))
        empty = [t for t in self.feed_types if not self.items.get(t)]
        if empty:
            problems.append("%s: a full read is empty (nothing to prove with)" % ", ".join(empty))
        return Check("full-read", problems, "%s; dropped 0; slowest page %.2f s" % (
            ", ".join(counts), max(self.slowest.values(), default=0)))

    def check_delta(self):
        declared = self.provider.describe().get("delta")  # what /v1/p says: a stored provider declares true whatever its class says
        problems = []
        for t in self.feed_types:
            token = self.tokens.get(t)
            if not token:
                continue
            code, body = self.get("/p/%s/%s" % (self.pid, t), since=FOREIGN)
            if code != 410:
                problems.append("%s: a foreign since token answered %s, not 410" % (t, code))
        if declared is False:
            for t in self.feed_types:
                if t in self.tokens:
                    code, body = self.get("/p/%s/%s" % (self.pid, t), since=self.tokens[t])
                    if code != 410:
                        problems.append("%s: delta is false but since=<sync_token> answered %s, not 410" % (t, code))
            return Check("delta", problems, "false: every since read is 410, a full read is the only read")
        if declared is not True:
            problems.append("delta is not declared, so it can't be tested")
            return Check("delta", problems, "")
        if self.remote:
            return Check("delta", problems, "true: a foreign since is 410; a change can't be made over HTTP (the rig's change() runs in-process)",
                         skipped=not problems)
        if self.change is None:
            problems.append("delta is true but the rig returns no change(): the kit can't make a change to read since")
            return Check("delta", problems, "")
        expected = self.change() or {}
        seen = []
        for t in self.feed_types:
            if t not in self.tokens:
                continue
            code, body = self.get("/p/%s/%s" % (self.pid, t), since=self.tokens[t])
            if code != 200:
                problems.append("%s: since=<sync_token> after a change answered %s" % (t, code))
                continue
            added, removed = expected.get(t, ((), ()))
            got = {i["id"] for i in body["items"]}
            missing = [i for i in added if i not in got]
            gone = [i for i in removed if i not in body["deleted"]]
            if missing:
                problems.append("%s: the change %s is not in the since read" % (t, missing[0]))
            if gone:
                problems.append("%s: the removal %s is not in deleted" % (t, gone[0]))
            if body["sync_token"] == self.tokens[t] and (added or removed):
                problems.append("%s: sync_token did not move after a change" % t)
            seen.append("%s +%d -%d" % (t, len(added), len(removed)))
        return Check("delta", problems, "true: since after a change returns it and its removal (%s)" % ", ".join(seen))

    def check_playable(self):
        problems, streams, counted = [], {}, 0
        for t, items in self.items.items():
            for item in items:
                shape = schema.kinds(t).get(item.get("kind"))
                allowed = schema.schema_for(shape).get("x-playable") if shape else None
                if allowed is True:
                    continue
                rest = item
                if allowed:
                    rest = {k: v for k, v in item.items() if k not in allowed}
                    for key in allowed:
                        for stream in item.get(key) or []:
                            if isinstance(stream, dict) and stream.get("url"):
                                streams.setdefault(stream["url"], item["id"])
                found = schema.playable(rest)
                counted += 1
                problems += ["%s %s: %s" % (t, item["id"], f) for f in found[:2]]
        insecure = [u for u in streams if not u.startswith("https://")]
        problems += ["%s: a stream that is not https (%s)" % (streams[u], u) for u in insecure[:3]]
        own = [u for u in streams if "/v1/play/" in u]  # the source's own gateway: a session, not a public HEAD
        public = [u for u in streams if u not in own and u not in insecure]
        if self.offline:
            return Check("playable", problems, "%d items hold nothing playable outside x-playable; %d stream URLs, HEAD skipped (--offline)"
                         % (counted, len(streams)), skipped=not problems and bool(public))
        answered = 0
        for url in public:
            status = head(url)
            if isinstance(status, int) and status < 400:
                answered += 1
            else:
                problems.append("%s: HEAD %s answered %s" % (streams[url], url, status))
        return Check("playable", problems, "%d items hold nothing playable outside x-playable; %d of %d stream URLs answer a HEAD%s"
                     % (counted, answered, len(public), ", %d on this source's /v1/play" % len(own) if own else ""))

    def resolves(self, ids):
        """The ids among `ids` the provider's own metadata answers for (ids= in batches)."""
        found = set()
        ids = sorted(set(ids))
        for start in range(0, len(ids), contracts.MAX_IDS):
            code, body = self.get("/p/%s/metadata" % self.pid, ids=",".join(ids[start:start + contracts.MAX_IDS]))
            if code == 200:
                found |= {i["id"] for i in body["items"]}
        return found

    def check_airings(self):
        airings = [i for i in self.items.get("availability") or [] if i.get("kind") == "airing"]
        if not airings:
            return Check("airings", [], "no airings")
        problems = []
        without = [a["id"] for a in airings if not a.get("work_id")]
        if without:
            problems.append("%d airings have no work_id (%s)" % (len(without), without[0]))
        wanted = {a["work_id"] for a in airings if a.get("work_id")}
        if "metadata" not in self.feed_types:
            problems.append("airings name works but the provider serves no metadata to resolve them in")
            return Check("airings", problems, "")
        found = self.resolves(wanted)
        missing = sorted(wanted - found)
        if missing:
            problems.append("%d of %d work ids do not resolve in %s metadata (%s)" % (len(missing), len(wanted), self.pid, missing[0]))
        return Check("airings", problems, "%d airings name %d works, all in this provider's metadata" % (len(airings), len(wanted)))

    def check_content(self):
        content = self.items.get("content") or []
        if not content:
            return Check("content", [], "no content" if "content" not in self.feed_types else "")
        problems = []
        availability = self.items.get("availability") or []
        if "availability" not in self.feed_types:
            problems.append("content without availability: no offer of the service to pair with")
        offers = [a for a in availability if a.get("kind") == "offer"]
        lineup = [a for a in availability if a.get("kind") == "channel"]
        own_works = set()
        if "metadata" in self.feed_types:
            own_works = self.resolves([i["title_id"] for i in content if i.get("title_id")]
                                      + [i["channel_id"] for i in content if i.get("channel_id")])
        strong, by_title = 0, 0
        for item in content:
            service, ids = item.get("service"), strong_ids(item)
            if item.get("kind") == "live":
                mates = [c for c in lineup if c.get("service") == service and (
                    c.get("channel_id") == item.get("channel_id") or strong_ids(c) & ids)]
                work = item.get("channel_id") in own_works or bool(item.get("ids"))
            else:
                mates = [o for o in offers if o.get("service") == service and (
                    (strong_ids(o) & ids) or (item.get("title_id") and o.get("title_id") == item["title_id"]))]
                work = bool(ids) or item.get("title_id") in own_works
            if not mates:
                problems.append("%s: no offer of service %s pairs with it by strong id or title_id" % (item["id"], service))
            if not work:
                problems.append("%s: no work: no strong id (%s) and title_id does not resolve in %s metadata"
                                % (item["id"], "/".join(STRONG), self.pid))
            if ids:
                strong += 1
            elif item.get("title_id"):
                by_title += 1
        return Check("content", problems, "%d items pair with an offer of their service and a work (%d by strong id, %d by title_id)"
                     % (len(content), strong, by_title))

    def check_licence(self):
        problems, count, overrides = [], 0, 0
        for t, items in self.items.items():
            for item in items:
                for path, art in arts(item):
                    count += 1
                    if not art.get("license"):
                        problems.append("%s %s: %s has no license" % (t, item["id"], path))
                if item.get("attribution"):
                    overrides += 1
                    if not item["attribution"].get("text"):
                        problems.append("%s %s: attribution overrides without text" % (t, item["id"]))
        attribution = self.provider.attribution or {}
        display = attribution.get("display", "about")
        if not attribution.get("text"):
            problems.append("the provider declares no attribution text")
        if display == "item" and not attribution.get("link"):
            problems.append("display is item but the attribution has no link to follow the credit")
        return Check("licence", problems, "%d art with a licence; attribution display %s%s" % (
            count, display, ", %d items override it" % overrides if overrides else ""))

    def check_lookups(self):
        problems, tried = [], 0
        for t, items in self.items.items():
            for item in items[:3]:
                asks = [("ids", item["id"])]
                for scheme, value in sorted((item.get("ids") or {}).items())[:1]:
                    asks.append(("ids", "%s:%s" % (scheme, value)))
                words = re.findall(r"\w+", str(item.get("title") or item.get("name") or ""))
                if words:
                    asks.append(("q", words[0]))
                for key, value in asks:
                    tried += 1
                    for route in ("/p/%s/%s" % (self.pid, t), "/p/-/%s" % t):
                        code, body = self.get(route, **{key: value})
                        if code != 200 or item["id"] not in {i["id"] for i in body["items"]}:
                            problems.append("%s %s: %s?%s=%s does not return it (%s)" % (t, item["id"], route, key, value, code))
        return Check("lookups", problems, "%d lookups by own id, canonical id and a title word return the item, on the route and across the view" % tried)

    def check_access(self):
        problems = []
        # over HTTP a public provider is in every view: without --other-token there is no ungranted view to read as
        no_other = bool(self.remote) and bool(self.provider.public) and not self.remote.other
        if not no_other:
            code, body = self.get("/p", seen=OTHER)
            if any(p["id"] == self.pid for p in body["providers"]):
                problems.append("listed in /v1/p for a view that doesn't hold it")
            if any(p["id"] == self.pid for p in self.get("/p", seen=set())[1]["providers"]):
                problems.append("listed in /v1/p for an empty view")
            for t in sorted(set(self.provider.types) | set(schema.PRIMARY) | {"nothing"}):
                code, body = self.get("/p/%s/%s" % (self.pid, t), seen=OTHER)
                if code != 404:
                    problems.append("/v1/p/%s/%s answers %s to a view that doesn't hold it, not 404" % (self.pid, t, code))
            for t, items in self.items.items():
                if items:
                    code, body = self.get("/p/-/%s" % t, seen=OTHER, ids=items[0]["id"])
                    if code != 200 or body["items"] or self.pid in body["providers"]:
                        problems.append("/v1/p/-/%s?ids= shows the item to a view that doesn't hold the provider" % t)

        try:
            import play  # the source's stream gateway, when this source has one
        except ImportError:
            play = None
        if self.remote:
            play = None  # the gateway is read in-process; a running source's is the host's
        if play is not None:
            def refuse(*a, **k):
                raise AssertionError("the stream gateway reached the direct service for an ungranted member")
            gateway = play.Gateway(self.source, direct="http://127.0.0.1:1", opener=refuse)
            for kind in play.KINDS:
                status = gateway.answer("/play/%s/%s/some-id.m3u8" % (self.pid, kind), "", set(OTHER))[0]
                if status != 404:
                    problems.append("/v1/play/%s/%s/... answers %s to a view that doesn't hold it, not 404" % (self.pid, kind, status))
            if gateway.answer("/play/s/some-session/index.m3u8", "", set(OTHER))[0] != 404:
                problems.append("/v1/play/s/... answers an ungranted member")
        code, body = self.get("/p")
        if not any(p["id"] == self.pid for p in body["providers"]):
            problems.append("not listed in /v1/p for a view that holds it")
        for t in self.feed_types:
            if self.get("/p/%s/%s" % (self.pid, t))[0] != 200:
                problems.append("/v1/p/%s/%s is not 200 for a view that holds it" % (self.pid, t))
        gate = "and on /v1/play" if play is not None else "(no stream gateway in this source)"
        if no_other:
            return Check("access", problems, "public: in every view, the ungranted half needs --other-token; granted: listed, every type 200",
                         skipped=not problems)
        return Check("access", problems, "ungranted: absent from /v1/p, 404 on every type %s; granted: listed, every type 200" % gate)

    def check_proof(self):
        problems = []
        name = "test_contract_%s.py" % self.pid
        test = next((d / name for d in (HERE.parent / "tests", HERE.parent) if (d / name).is_file()), HERE.parent / "tests" / name)
        if not test.is_file():
            problems.append("no %s in tests/" % name)
        fixture = FIXTURES / (self.pid + ".json")
        where = "fixture contracts/fixtures/%s.json" % self.pid if fixture.is_file() else "the rig builds from the provider's own data"
        return Check("proof", problems, "rig contracts/%s.py, %s, %s" % (self.pid, where, test.name))

    def run(self):
        """Every check, in the definition of done's order (the delta check, which changes the data, runs last)."""
        self.checks["declaration"] = self.check_declaration()
        self.checks["full-read"] = self.check_full_read()
        for name, check in (("playable", self.check_playable), ("airings", self.check_airings), ("content", self.check_content),
                            ("licence", self.check_licence), ("lookups", self.check_lookups), ("access", self.check_access),
                            ("delta", self.check_delta), ("proof", self.check_proof)):
            try:
                self.checks[name] = check()
            except Exception as error:  # a check that crashes is a failure with the reason, not a crash of the kit
                self.checks[name] = Check(name, ["the check raised %s: %s" % (type(error).__name__, error)], "")
        return [self.checks[name] for name in ORDER]


def run(pid, offline=False, now=None, budget=5.0):
    """The checks for one provider: [Check] in the definition of done's order."""
    now = int(now or time.time())
    with tempfile.TemporaryDirectory(prefix="conform-%s-" % pid) as directory:
        try:
            provider, change = build(pid, directory, now)
        except (ImportError, LookupError) as error:
            return [Check("proof", [str(error)], "")]
        return Run(pid, provider, change, now, offline=offline, budget=budget).run()


def run_url(pid, url, token=None, other=None, offline=False, budget=5.0):
    """The checks for one provider read over HTTP from the source at `url` (contracts/serve.py)."""
    remote = Remote(url, pid, token, other)
    try:
        provider = declared(remote)
    except (LookupError, URLError, OSError, ValueError) as error:
        return [Check("declaration", [str(error)], "")]
    return Run(pid, provider, None, time.time(), offline=offline, budget=budget, remote=remote).run()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m contracts.conform", description="Is this provider on the contract? (docs/contract/PROVIDERS.md §6)")
    parser.add_argument("provider", nargs="*", help="provider ids (contracts/<id>.py with a rig)")
    parser.add_argument("--all", action="store_true", help="every provider with a rig")
    parser.add_argument("--offline", action="store_true", help="skip the HEAD of each stream URL (the only network)")
    parser.add_argument("--budget", type=float, default=5.0, help="seconds a page may take (default 5)")
    parser.add_argument("--url", help="read a running source over HTTP instead of the rig, e.g. http://127.0.0.1:8080")
    parser.add_argument("--token", help="with --url: a bearer token whose view holds the provider")
    parser.add_argument("--other-token", help="with --url: a bearer token whose view doesn't (default: no token)")
    args = parser.parse_args(argv)
    ids = rigs() if args.all else args.provider
    if args.url and args.all:
        parser.error("--all runs the rigs; with --url name the providers")
    if not ids:
        parser.error("name a provider, or --all (rigs: %s)" % ", ".join(rigs()))
    failed = []
    for pid in ids:
        if args.url:
            checks = run_url(pid, args.url, args.token, args.other_token, offline=args.offline, budget=args.budget)
        else:
            checks = run(pid, offline=args.offline, budget=args.budget)
        for check in checks:
            print(check.line(pid))
        bad = [c.name for c in checks if not c.ok]
        if bad:
            failed.append(pid)
        print("%s: %s" % (pid, "passes" if not bad else "fails " + ", ".join(bad)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
