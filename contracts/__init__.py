"""Provider contracts: the server side of docs/contract/.

Three primary types: **metadata** (what works are), **availability** (which service, channel or M3U has
which works, and when: catalogue windows, a service's channels, airings) and **content** (delivering a service's
items to a member). An item's `kind` picks its shape (contracts/schema.py); `subcategories` (movies, tv-shows,
news, documentaries, channels, apps, ...) is a field within each type, never a type.

A **provider** declares who it is and which types it implements; this package does the rest of the contract
for every provider the same way: the envelope, paging, `since` delta reads, region, point lookups by id, the
kind and subcategory filters, the airing window, schema checks and the metadata-only rule.

    class MyProvider(contracts.Provider):
        id, name = "myprovider", "My provider"
        types = ("metadata",)
        subcategories = ("movies",)
        regions, trust, public = ("US",), 40, False
        attribution = {"text": "...", "link": "...", "license": "...", "license_link": "...", "display": "about"}

        def changed_at(self, contract_type):        # when its data last changed (epoch seconds), or None
            ...
        def metadata(self, query):                  # one method per type: metadata, availability, content
            return [item, ...]                      # every current item, contract-shaped (§2)

`delta`: True when a `since` read answers what changed plus `deleted` (then implement
`removed_since(contract_type, since)` -> ids deleted after `since`, [] when none, or None when it can't tell: the
source answers 410 and the app reads everything again); False when every `since` is 410 and a full read is the
only read. The envelope counts the items its schema dropped in `dropped`; the conformance kit
(`python3 -m contracts.conform <id>`, docs/contract/PROVIDERS.md §6) fails on any. `cache_reads = N`: a big provider's
items are built once per read and its pages cut from that for N seconds (while the sync token holds).
`playable(kind, ident)`: a content provider whose streams must stay on the box registers a resolver for the
stream gateway (play.py: /v1/play/<provider>/<kind>/<id>.m3u8, kinds channel, movie, episode).

Who may read a provider is not decided here: the source's auth layer answers the member's view (X-Orman-View)
and `Source.answer(..., seen)` serves a provider only when it is in that view, else 404, the same as a provider
that doesn't exist. The routes, under the channel service's /v1/ (nginx: auth_request /auth/check/app):

  GET /v1/p                        {contracts, providers: [descriptor]}: the providers in the member's view
  GET /v1/p/{provider}/{type}      the envelope (§2): ?limit&cursor&since&region&ids&kind&subcategory,
                                   availability also ?from&to&channels (the airing window)
  GET /v1/p/-/{type}?q=|ids=       a lookup across every provider in the view: "what is it, which channel airs it
                                   and when, which service has it now" (each item tagged with its provider)
  GET /v1/p/{provider}/content.movie   the draft name of content?kind=movie, kept as an alias
"""
import base64
from datetime import datetime, timezone
import binascii
import json
import logging
import re
import threading
import time

from contracts import schema
from contracts.schema import clean, schema_for

LOG = logging.getLogger("orman-contracts")
READS_LOCK = threading.Lock()
ALIASES = {"content.movie": ("content", "movie")}  # an earlier name: (type, kind)


def version(contract_type):
    return schema_for(contract_type).get("x-version", 1)


def ttl(contract_type):
    return schema_for(contract_type).get("x-ttl", 3600)


DEFAULT_LIMIT, MAX_LIMIT = 500, 2000
MEMBER_TYPES = ("reminders", "storage", "tvcontrol")  # written by the member, served where they are kept (the source's member store)
EPG_DEFAULT, EPG_MAX = 86400, 7 * 86400
READ_BUCKET = 300  # seconds of from/to that name one kept read
REGION = re.compile(r"[A-Z]{2}")
PROVIDER_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
MAX_IDS = 500


def iso(ts):
    """Epoch seconds as ISO 8601 UTC, to the second: 2026-10-14T07:00:00Z."""
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text):
    """ISO 8601 (a trailing Z allowed) or epoch seconds -> epoch seconds; ValueError otherwise."""
    text = (text or "").strip()
    if re.fullmatch(r"\d{9,11}", text):
        return int(text)
    when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return int(when.timestamp())


class BadRequest(Exception):
    """400: a parameter the contract doesn't accept."""


class Gone(Exception):
    """410: `since` (or a cursor) is too old for a delta; read everything again."""


class Query:
    """One request's parameters, read and checked: what a provider's type method gets."""

    def __init__(self, provider, contract_type, params, now=None):
        get = lambda key: (params.get(key) or [""])[0].strip()
        self.type, self.now = contract_type, int(now or time.time())
        try:
            self.limit = int(get("limit") or DEFAULT_LIMIT)
        except ValueError:
            raise BadRequest("limit must be a number") from None
        if not 1 <= self.limit <= MAX_LIMIT:
            raise BadRequest("limit must be 1 to %d" % MAX_LIMIT)
        self.region = get("region").upper() or provider.regions[0]
        if not REGION.fullmatch(self.region):
            raise BadRequest("region must be an ISO 3166-1 alpha-2 code")
        raw_ids = [i.strip() for i in get("ids").split(",") if i.strip()]
        if len(raw_ids) > MAX_IDS:
            raise BadRequest("at most %d ids" % MAX_IDS)
        self.ids = set(raw_ids)
        self.cursor, self.since = get("cursor"), get("since")
        self.channels = {c.strip() for c in get("channels").split(",") if c.strip()}
        self.q = [w for w in re.findall(r"\w+", get("q").casefold())][:10]
        self.kinds = {k.strip() for k in get("kind").split(",") if k.strip()}
        self.subcategories = {c.strip() for c in get("subcategory").split(",") if c.strip()}
        self.start = self.end = None
        window = (get("from"), get("to"))
        if contract_type == "availability":  # the window airings are read in
            try:
                self.start = parse_time(get("from")) if get("from") else self.now
                self.end = parse_time(get("to")) if get("to") else self.start + EPG_DEFAULT
            except ValueError:
                raise BadRequest("from and to must be ISO 8601 times") from None
            if not 0 < self.end - self.start <= EPG_MAX:
                raise BadRequest("the airing window is after from and at most 7 days")
            # a paging client's clock drifts by seconds: its pages share one kept build (envelope's key)
            window = tuple(t // READ_BUCKET if get(k) else "" for k, t in (("from", self.start), ("to", self.end)))
        filters = (self.region, tuple(sorted(self.ids)), tuple(self.q), tuple(sorted(self.kinds)),
                   tuple(sorted(self.subcategories)), tuple(sorted(self.channels)))
        self.asked = filters + window
        # a later page that drifted over a bucket edge still finds page 1's build
        self.asked_before = filters + tuple(w - 1 if w != "" else w for w in window) if self.start else None


class Provider:
    """A metadata provider: declare these, and implement one method per type in `types`."""
    id = ""             # "freemovies": the id in "<provider>:<local id>" and the route /v1/p/<id>/<type>
    name = ""           # "Free movies"
    types = ()          # the primary types it implements: "metadata", "availability", "content"
    subcategories = ()  # what it covers, from the vocabulary: "movies", "tv-shows", "news", "channels", ...
    regions = ("US",)   # ISO 3166-1 alpha-2; the first is the default region
    trust = 50          # 0-100, set by the source's owner: breaks field conflicts in the merge (§3.3)
    attribution = None  # {"text", "link", "license", "license_link", "display": "about"|"item"}
    notice = ""         # "Not affiliated with ..."
    public = False      # the provider's own declaration; who is granted it is the source's auth layer
    delta = None        # True: a `since` read answers what changed and `deleted`; False: every `since` is 410 (a full
                        # read is the only read). The declaration decides, not the data: conform tests it.
    cache_reads = 0     # seconds one read's items serve its later pages (0: built for every request)
    store = None        # a contracts.store.Store this provider was synced into: reads come from it, delta is True

    def changed_at(self, contract_type):
        """When this type's data last changed (epoch seconds), or None when unknown."""
        return None

    def removed_since(self, contract_type, since):
        """Ids removed after `since`; None when the provider can't tell (the answer is then 410)."""
        return None

    def handler(self, contract_type):
        return getattr(self, contract_type)

    def sync(self, store, now=None):
        """The write path (contracts/store.py): every feed type read whole through its type method, cleaned,
        upserted, what vanished written as a tombstone. From then on this provider answers from the store. A
        provider that can write its store more cleverly (a delta from its upstream) overrides this."""
        return store.sync(self, now)

    def overlays(self, widget, now):
        """A widget provider's current overlay messages for one of its `pull` widgets (docs/widget-engine.md §7):
        [message, ...] in the engine's envelope, read by the broker when an app connects. None: nothing to show."""
        return None

    def describe(self, access=None):
        out = {"id": self.id, "name": self.name, "access": access or ("public" if self.public else "private"),
               "types": list(self.types), "subcategories": list(self.subcategories), "regions": list(self.regions),
               "trust": self.trust}
        if self.attribution:
            out["attribution"] = dict(self.attribution)
        if self.notice:
            out["notice"] = self.notice
        if self.store is not None:
            out["delta"] = True  # a stored provider serves `since` from updated_at and tombstones, whatever it declares
        elif self.delta is not None:
            out["delta"] = bool(self.delta)
        return out


TEXT = ("title", "name", "series_title", "episode_title", "channel_name")


def found_by(item, words):
    """Every word of a search is the start of a word in the item's title, name, series or channel."""
    hay = re.findall(r"\w+", " ".join(str(item.get(k) or "") for k in TEXT).casefold())
    return all(any(h.startswith(w) for h in hay) for w in words)


def current(item, now):
    """False for an item whose window has closed (an offer that left, an airing that ended)."""
    end = (item.get("window") or {}).get("end") or item.get("end")
    try:
        return not end or parse_time(end) > now
    except ValueError:
        return True


def matches(item, wanted, provider_id):
    """A point lookup: an item id ("freemovies:Q1"), or a canonical id ("wikidata:Q1", "imdb:tt0063350")."""
    if item.get("id") in wanted:
        return True
    for scheme, value in (item.get("ids") or {}).items():
        if "%s:%s" % (scheme, value) in wanted:
            return True
    return any(item.get(k) in wanted for k in ("title_id", "channel_id", "work_id", "series_id") if item.get(k))


def cursor_out(offset, token):
    raw = json.dumps({"o": offset, "t": token}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def cursor_in(text):
    try:
        raw = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
        return int(raw["o"]), str(raw["t"])
    except (binascii.Error, ValueError, KeyError, TypeError):
        raise BadRequest("cursor is not one this source gave") from None


def sync_token(changed):
    return "s_" + (iso(changed) if changed else "0")


def token_time(token):
    """The time a sync_token stands for; Gone when it isn't one of ours."""
    if not token.startswith("s_"):
        raise Gone("since is not a sync_token from this source")
    if token == "s_0":
        return 0
    try:
        return parse_time(token[2:])
    except ValueError:
        raise Gone("since is not a sync_token from this source") from None


def updated(item):
    try:
        return parse_time(item.get("updated_at"))
    except (TypeError, ValueError):
        return 0


def envelope(provider, contract_type, query):
    """The contract's answer for one provider and type (§2)."""
    if provider.store is not None and provider.store.synced(provider.id, contract_type):
        return provider.store.read(provider, contract_type, query)  # indexed SELECTs; the in-memory build below is the fallback
    if query.since and provider.delta is False:  # declared: no delta is served, whatever the data could tell, and
        raise Gone("this provider serves no delta; read everything again")  # nothing is built or asked for a 410
    changed = provider.changed_at(contract_type)
    token = sync_token(changed)
    offset = 0
    if query.cursor:
        offset, started = cursor_in(query.cursor)
        if started != token:
            raise Gone("the data changed while paging; read it again")
    deleted = []
    items, dropped = None, 0
    reads = provider.cache_reads
    key = (contract_type, token, query.asked)
    if reads and not query.since:  # a big provider: the pages of one read come from one build of its items
        kept = provider.__dict__.setdefault("_reads", {})
        hit = kept.get(key)
        if not hit and query.cursor and query.asked_before:
            hit = kept.get((contract_type, token, query.asked_before))
        if hit and query.now - hit[0] < reads:
            items, dropped = hit[1], hit[2]
    if query.since:
        since = token_time(query.since)
        if changed is not None and since >= int(changed):
            items = []  # nothing changed since then
        else:
            removed = provider.removed_since(contract_type, since)
            if removed is None:
                raise Gone("since is too old for a delta; read everything again")
            deleted = sorted(removed)
    if items is None:
        items = []
        for raw in provider.handler(contract_type)(query) or []:
            # Detail/Play reads must not validate an entire large catalogue to return a few references.
            # These are only inexpensive rejection checks; candidates still pass the schema and all filters
            # below, so invalid fields or fields the schema removes can never make an item visible.
            if isinstance(raw, dict):
                if query.kinds and raw.get("kind") not in tuple(query.kinds):
                    continue
                # clean() keeps supported text verbatim and only removes fields: it cannot create a search
                # match. The post-clean filter stays too, since a removed field may have matched raw text.
                if query.q and not found_by(raw, query.q):
                    continue
                if query.ids:
                    try:
                        if not matches(raw, query.ids, provider.id):
                            continue
                    except (AttributeError, TypeError):
                        pass  # malformed provider data still goes through schema validation and logging
            item, errors = clean(contract_type, raw)
            if errors:  # a provider bug never reaches a member; the journal says what was wrong
                LOG.warning("%s %s: dropped %s: %s", provider.id, contract_type,
                            raw.get("id") if isinstance(raw, dict) else None, "; ".join(errors[:3]))
                dropped += 1
                continue
            if not str(item["id"]).startswith(provider.id + ":"):
                LOG.warning("%s %s: dropped %s: not this provider's id", provider.id, contract_type, item["id"])
                dropped += 1
                continue
            items.append(item)
        if query.since:
            since = token_time(query.since)
            items = [i for i in items if updated(i) > since]
        if query.ids:
            items = [i for i in items if matches(i, query.ids, provider.id)]
        if query.q:
            items = [i for i in items if found_by(i, query.q)]
        items = [i for i in items if current(i, query.now)]
        if query.kinds:
            items = [i for i in items if i["kind"] in query.kinds]
        if query.subcategories:
            items = [i for i in items if query.subcategories & set(i.get("subcategories") or ())]
        items.sort(key=lambda i: str(i["id"]))
        if reads and not query.since:
            with READS_LOCK:
                kept = provider.__dict__["_reads"]
                for old in [k for k, (at, *_) in kept.items() if query.now - at >= reads or k[1] != token]:
                    kept.pop(old, None)
                while len(kept) >= 4:  # a few reads at once (a window, a lookup), never a growing pile
                    kept.pop(next(iter(kept)))
                kept[key] = (query.now, items, dropped)
    if query.region not in provider.regions:
        items, deleted = [], []  # an item outside the provider's regions isn't shown (§4)
    page = items[offset:offset + query.limit]
    more = offset + len(page) < len(items)
    return {"provider": provider.id, "type": contract_type, "version": version(contract_type),
            "region": query.region, "generated_at": iso(query.now), "ttl": ttl(contract_type),
            "items": page, "deleted": deleted if offset == 0 else [], "dropped": dropped,
            "next_cursor": cursor_out(offset + len(page), token) if more else None,
            "sync_token": token}


class Source:
    """The providers this source hosts, answering /v1/p and /v1/p/{provider}/{type}."""

    def __init__(self, providers, store=None):
        self.providers = {p.id: p for p in providers}
        if store is not None:  # every provider the store holds answers from it (contracts/store.py)
            for provider in providers:
                if store.synced(provider.id):
                    provider.store = store

    def contracts(self):
        """{type: version} for every type a hosted provider implements."""
        served = {t for p in self.providers.values() for t in p.types}
        return {t: version(t) for t in schema.types() if t in served}

    def find(self, contract_type, params, seen, now=None):
        """(status, body) for /p/-/{type}: one lookup (q or ids) across every provider in the view that serves
        the type; each item carries `provider`. No paging or since: it answers a question, it doesn't sync."""
        get = lambda key: (params.get(key) or [""])[0].strip()
        if contract_type not in schema.types():
            return 404, {"error": "not_found"}
        if not (get("q") or get("ids")):
            return 400, {"error": "bad_request", "message": "a lookup across providers needs q or ids"}
        if get("cursor") or get("since"):
            return 400, {"error": "bad_request", "message": "a lookup across providers has no cursor or since"}
        items, asked, dropped = [], [], 0
        for provider in self.visible(seen):
            if contract_type not in provider.types or contract_type in MEMBER_TYPES:
                continue
            asked.append(provider.id)
            try:
                body = envelope(provider, contract_type, Query(provider, contract_type, params, now))
            except BadRequest as error:
                return 400, {"error": "bad_request", "message": str(error)}
            except Exception:
                LOG.exception("%s %s lookup failed", provider.id, contract_type)
                continue
            items += [dict(i, provider=provider.id) for i in body["items"]]
            dropped += body["dropped"]
        if contract_type == "availability":  # what a service has now first, then airings soonest first
            items.sort(key=lambda i: (i.get("start") or "", i["provider"], i["id"]))
        return 200, {"provider": "-", "providers": asked, "type": contract_type, "version": version(contract_type),
                     "generated_at": iso(now or time.time()), "ttl": ttl(contract_type), "items": items[:2000],
                     "dropped": dropped}

    def pull(self, pid, params, seen, now=None):
        """(status, body) for /p/{provider}/overlays?widget=<id>: a pull widget's current overlays, for the broker on
        loopback (seen None). Through nginx (a member's view) it doesn't exist."""
        provider = self.providers.get(pid)
        widget = (params.get("widget") or [""])[0].strip()
        if seen is not None or provider is None or "widgets" not in provider.types or not widget:
            return 404, {"error": "not_found"}
        try:
            found = provider.overlays(widget, int(now or time.time()))
        except Exception:
            LOG.exception("%s overlays %s failed", pid, widget)
            return 503, {"error": "unavailable"}
        return 200, {"provider": pid, "widget": widget, "overlays": found or []}

    def visible(self, seen):
        """The providers in this member's view (seen None: loopback, every provider)."""
        return [p for pid, p in self.providers.items() if seen is None or pid in seen]

    def answer(self, path, params, seen, now=None):
        """(status, body) for a path under /v1: "/p" or "/p/<provider>/<type>"."""
        if path in ("/p", "/p/"):
            return 200, {"contracts": self.contracts(),
                         "providers": [p.describe(None if seen is None else "granted") for p in self.visible(seen)]}
        parts = path.split("/")
        if len(parts) != 4 or parts[1] != "p":
            return 404, {"error": "not_found"}
        pid, contract_type = parts[2], parts[3]
        if contract_type == "overlays":  # the broker's pull (widget-engine §7): loopback only, never a member's
            return self.pull(pid, params, seen, now)
        if contract_type in ALIASES:
            contract_type, kind = ALIASES[contract_type]
            params = dict(params, kind=[kind])
        if pid == "-":
            return self.find(contract_type, params, seen, now)
        provider = self.providers.get(pid) if PROVIDER_ID.fullmatch(pid) else None
        if provider is None or (seen is not None and pid not in seen):  # ungranted is invisible
            return 404, {"error": "not_found"}
        if contract_type not in provider.types:
            return 404, {"error": "not_found", "message": "This provider doesn't serve that type."}
        if contract_type in MEMBER_TYPES:  # a member's own: reminders.py, storage.py
            return 404, {"error": "not_found", "message": "A member's %s are served by the widget broker." % contract_type}
        try:
            return 200, envelope(provider, contract_type, Query(provider, contract_type, params, now))
        except BadRequest as error:
            return 400, {"error": "bad_request", "message": str(error)}
        except Gone as error:
            return 410, {"error": "gone", "message": str(error)}
        except Exception:
            LOG.exception("%s %s failed", pid, contract_type)
            return 503, {"error": "unavailable", "message": "This provider can't answer right now."}
