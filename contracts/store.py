"""The store: a provider's items in SQLite, written once at sync, read as indexed SELECTs (docs/contract/STORE.md).

    python3 -m contracts.store sync <id> [<id> ...] [--db source.db]     read every feed type whole, write the store

Today `contracts.Source` builds a provider's whole list in Python per read and slices a page, and no provider
can serve a `since` delta. With the store the server is the database: `Store.sync(provider)` calls the
provider's type methods once, runs `schema.clean` on every item (a failing item is counted in `dropped`,
logged, never stored), upserts what changed and writes a tombstone for every id that vanished. A read never
validates: it is one SELECT with a keyset cursor on `(updated_at, id)`, `limit`, `ids`, `kind`, `subcategory`,
`q` (an FTS5 prefix match on title, name, series and channel name) and the airing window `from/to/channels`
from the airings table. `since` is `updated_at > token` plus the tombstones written after it, so a stored
provider declares `delta: true` whatever its class says. A token from before the first sync, from after the
latest, not one of ours, or older than the tombstones the store keeps (`KEEP`) is 410: read everything again.

The envelope, routes and item rules are the contract's (ENVELOPES.md); `dropped` is the latest sync's count.
`updated_at` in the items table is the sync that last changed the row, so a delta never misses an item whose
provider forgot to bump its own `updated_at`; the item served is the item the provider gave. A provider that
is not synced is served as before, from its type methods. Stdlib only: sqlite3 in WAL mode, FTS5.
"""
import argparse
import base64
import binascii
import json
import logging
import re
import sqlite3
import sys
import threading
import time

import contracts
from contracts import schema
from contracts.schema import clean

LOG = logging.getLogger("orman-contracts")
KEEP = 30 * 86400  # seconds a tombstone is kept: a token older than that is 410
DEFAULT_DB = "source.db"
AIRING = "airing"
SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    ref INTEGER PRIMARY KEY,                  -- the row number keys, tags and search point at (stable under VACUUM)
    provider TEXT NOT NULL, type TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
    updated_at INTEGER NOT NULL,              -- the sync that last changed this row (epoch seconds)
    json TEXT NOT NULL,                       -- the item as served, cleaned to its shape
    UNIQUE (provider, type, id));
CREATE INDEX IF NOT EXISTS items_updated ON items (provider, type, updated_at, id);
CREATE INDEX IF NOT EXISTS items_kind ON items (provider, type, kind, updated_at, id);
CREATE TABLE IF NOT EXISTS airings (
    provider TEXT NOT NULL, channel TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    PRIMARY KEY (provider, item_id));
CREATE INDEX IF NOT EXISTS airings_channel ON airings (provider, channel, start, item_id);
CREATE INDEX IF NOT EXISTS airings_start ON airings (provider, start, item_id);
CREATE TABLE IF NOT EXISTS tombstones (
    provider TEXT NOT NULL, type TEXT NOT NULL, id TEXT NOT NULL, deleted_at INTEGER NOT NULL,
    PRIMARY KEY (provider, type, id));
CREATE INDEX IF NOT EXISTS tombstones_at ON tombstones (provider, type, deleted_at);
CREATE TABLE IF NOT EXISTS keys (                 -- what ids= finds: the item's id, each ids scheme:value, its references
    provider TEXT NOT NULL, type TEXT NOT NULL, key TEXT NOT NULL, item INTEGER NOT NULL,
    PRIMARY KEY (provider, type, key, item)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS keys_item ON keys (item);
CREATE TABLE IF NOT EXISTS tags (                 -- what subcategory= finds
    provider TEXT NOT NULL, type TEXT NOT NULL, tag TEXT NOT NULL, item INTEGER NOT NULL,
    PRIMARY KEY (provider, type, tag, item)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS tags_item ON tags (item);
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(text, tokenize='unicode61');  -- rowid = items.ref
CREATE TABLE IF NOT EXISTS syncs (                -- the latest sync of one provider's type
    provider TEXT NOT NULL, type TEXT NOT NULL,
    token INTEGER NOT NULL,                       -- the sync_token: the last sync that changed anything
    first INTEGER NOT NULL,                       -- the first sync: an older since is unknown
    synced_at INTEGER NOT NULL, count INTEGER NOT NULL, dropped INTEGER NOT NULL,
    longest INTEGER NOT NULL,                     -- the longest airing, so a window read is one index range
    PRIMARY KEY (provider, type));
"""
REFERENCES = ("title_id", "channel_id", "work_id", "series_id")  # the fields a point lookup also matches (contracts.matches)


def canonical(item):
    return json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def text_of(item):
    """What `q` searches: the item's title, name, series and channel name (contracts.TEXT)."""
    return " ".join(str(item.get(k) or "") for k in contracts.TEXT).strip()


def keys_of(item):
    found = {str(item["id"])}
    for scheme, value in (item.get("ids") or {}).items():
        found.add("%s:%s" % (scheme, value))
    for k in REFERENCES:
        if item.get(k):
            found.add(str(item[k]))
    return found


def match_expr(words):
    """An FTS5 query: every word a prefix, all required (contracts.found_by)."""
    parts = []
    for word in words:
        for token in re.findall(r"[^\W_]+", word):  # unicode61 breaks words where \w doesn't
            parts.append('"%s"*' % token)
    return " ".join(parts)


def cursor_out(section, key, token):
    raw = json.dumps({"s": section, "k": list(key), "t": token}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def cursor_in(text, token):
    try:
        raw = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
        section, (a, b), started = int(raw["s"]), raw["k"], str(raw["t"])
        key = (int(a), str(b))
    except (binascii.Error, ValueError, KeyError, TypeError):
        raise contracts.BadRequest("cursor is not one this source gave") from None
    if started != token:
        raise contracts.Gone("the data changed while paging; read it again")
    return section, key


def placeholders(values):
    return ",".join("?" * len(values))


class Store:
    """One SQLite file (or ":memory:") holding any number of providers."""

    def __init__(self, path=DEFAULT_DB):
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()  # one connection, used from the server's threads one at a time
        self.db.execute("PRAGMA busy_timeout = 5000")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA synchronous = NORMAL")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # the write path

    def state(self, pid, contract_type):
        """The syncs row for one provider's type, or None when it was never synced."""
        with self.lock:
            return self.db.execute("SELECT * FROM syncs WHERE provider = ? AND type = ?", (pid, contract_type)).fetchone()

    def synced(self, pid, contract_type=None):
        """Whether this provider's type (or any type) has been synced into this store."""
        with self.lock:
            if contract_type is None:
                return bool(self.db.execute("SELECT 1 FROM syncs WHERE provider = ? LIMIT 1", (pid,)).fetchone())
            return bool(self.db.execute("SELECT 1 FROM syncs WHERE provider = ? AND type = ?", (pid, contract_type)).fetchone())

    def sync(self, provider, now=None):
        """Every feed type of `provider` read whole and written: {type: {"items", "added", "changed", "removed",
        "dropped", "token"}}. Afterwards the provider answers from this store (provider.store)."""
        now = int(now or time.time())
        report = {}
        for contract_type in provider.types:
            if contract_type in contracts.MEMBER_TYPES or contract_type not in schema.types():
                continue
            query = contracts.Query(provider, contract_type, {"limit": [str(contracts.MAX_LIMIT)], "from": [contracts.iso(now)],
                                                             "to": [contracts.iso(now + contracts.EPG_MAX)]}, now)
            raw = list(provider.handler(contract_type)(query) or [])
            report[contract_type] = self.write(provider.id, contract_type, raw, now)
        provider.store = self
        return report

    def write(self, pid, contract_type, raw_items, now=None):
        """One type of one provider replaced by `raw_items` (the provider's own output, cleaned here), in one
        transaction. Unchanged rows keep their updated_at; changed and new rows take this sync's time; rows
        not in the list become tombstones. Returns the summary `sync` reports."""
        now = int(now or time.time())
        dropped, longest = 0, 0
        with self.lock:
            db = self.db
            db.execute("BEGIN IMMEDIATE")
            try:
                state = db.execute("SELECT * FROM syncs WHERE provider = ? AND type = ?", (pid, contract_type)).fetchone()
                last = state["token"] if state else 0
                at = max(now, last + 1)  # strictly after the last token, so `updated_at > token` finds this sync
                have = {row["id"]: (row["ref"], row["json"])
                        for row in db.execute("SELECT ref, id, json FROM items WHERE provider = ? AND type = ?", (pid, contract_type))}
                ref = (db.execute("SELECT COALESCE(MAX(ref), 0) FROM items").fetchone()[0])
                seen, added, changed = set(), [], []  # rows: (ref, kind, id, json)
                keys, tags, search, airings = [], [], [], []
                for raw in raw_items:
                    item, errors = clean(contract_type, raw)
                    if not errors and not str(item.get("id", "")).startswith(pid + ":"):
                        errors = ["not this provider's id"]
                    if not errors and item["id"] in seen:
                        errors = ["the id repeats in one sync"]
                    airing = None
                    if not errors and item["kind"] == AIRING:
                        try:
                            airing = (item["channel_id"], contracts.parse_time(item["start"]), contracts.parse_time(item["end"]))
                        except (KeyError, ValueError):
                            errors = ["start and end are not times"]
                    if errors:  # a provider bug never reaches a member; the journal says what was wrong
                        LOG.warning("%s %s: dropped %s: %s", pid, contract_type,
                                    raw.get("id") if isinstance(raw, dict) else None, "; ".join(errors[:3]))
                        dropped += 1
                        continue
                    if not contracts.current(item, now):
                        continue  # a closed window is not served (§4), so it isn't stored; a stored one that closed is removed below
                    seen.add(item["id"])
                    if airing:
                        longest = max(longest, airing[2] - airing[1])
                    text = canonical(item)
                    if item["id"] in have:
                        if have[item["id"]][1] == text:
                            continue
                        row = (have[item["id"]][0], item["kind"], item["id"], text)
                        changed.append(row)
                    else:
                        ref += 1
                        row = (ref, item["kind"], item["id"], text)
                        added.append(row)
                    keys += [(pid, contract_type, k, row[0]) for k in keys_of(item)]
                    tags += [(pid, contract_type, t, row[0]) for t in set(item.get("subcategories") or ())]
                    words = text_of(item)
                    if words:
                        search.append((row[0], words))
                    if airing:
                        airings.append((pid, airing[0], airing[1], airing[2], item["id"]))
                gone = sorted(set(have) - seen)
                stale = [(have[i][0],) for i in gone] + [(r[0],) for r in changed]
                for table, column in (("keys", "item"), ("tags", "item"), ("search", "rowid")):
                    db.executemany("DELETE FROM %s WHERE %s = ?" % (table, column), stale)
                db.executemany("DELETE FROM airings WHERE provider = ? AND item_id = ?", [(pid, i) for i in gone] + [(pid, r[2]) for r in changed])
                db.executemany("DELETE FROM items WHERE ref = ?", [(have[i][0],) for i in gone])
                db.executemany("INSERT OR REPLACE INTO tombstones (provider, type, id, deleted_at) VALUES (?, ?, ?, ?)",
                               [(pid, contract_type, i, at) for i in gone])
                db.executemany("UPDATE items SET kind = ?, updated_at = ?, json = ? WHERE ref = ?",
                               [(kind, at, text, r) for r, kind, i, text in changed])
                db.executemany("INSERT INTO items (ref, provider, type, kind, id, updated_at, json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                               [(r, pid, contract_type, kind, i, at, text) for r, kind, i, text in added])
                db.executemany("DELETE FROM tombstones WHERE provider = ? AND type = ? AND id = ?", [(pid, contract_type, r[2]) for r in added])
                db.executemany("INSERT OR IGNORE INTO keys (provider, type, key, item) VALUES (?, ?, ?, ?)", keys)
                db.executemany("INSERT OR IGNORE INTO tags (provider, type, tag, item) VALUES (?, ?, ?, ?)", tags)
                db.executemany("INSERT INTO search (rowid, text) VALUES (?, ?)", search)
                db.executemany("INSERT INTO airings (provider, channel, start, end, item_id) VALUES (?, ?, ?, ?, ?)", airings)
                db.execute("DELETE FROM tombstones WHERE provider = ? AND type = ? AND deleted_at < ?", (pid, contract_type, at - KEEP))
                moved = bool(added or changed or gone or not state)
                token = at if moved else last
                if state and not moved:
                    longest = max(longest, state["longest"])
                db.execute("INSERT OR REPLACE INTO syncs (provider, type, token, first, synced_at, count, dropped, longest) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (pid, contract_type, token, state["first"] if state else at, at, len(seen), dropped, longest))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("PRAGMA optimize")
        report = {"items": len(seen), "added": len(added), "changed": len(changed), "removed": len(gone), "dropped": dropped,
                  "token": contracts.sync_token(token)}
        LOG.info("%s %s: %d items (+%d ~%d -%d), dropped %d, token %s", pid, contract_type, len(seen), len(added), len(changed),
                 len(gone), dropped, report["token"])
        return report

    # the read path

    def read(self, provider, contract_type, query):
        """The envelope for one stored type (contracts.envelope's answer, from the store)."""
        state = self.state(provider.id, contract_type)
        if state is None:
            raise LookupError("%s %s is not in the store" % (provider.id, contract_type))
        token = contracts.sync_token(state["token"])
        section, key = cursor_in(query.cursor, token) if query.cursor else (0, None)
        since, deleted = None, []
        if query.since:
            since = contracts.token_time(query.since)
            # unknown: before the first sync or after the latest token; too old: before the tombstones kept (KEEP before
            # the latest sync), unless it is the latest token itself, after which nothing was deleted
            floor = max(state["first"], min(state["token"], state["synced_at"] - KEEP))
            if since > state["token"] or since < floor:
                raise contracts.Gone("since is too old for a delta; read everything again")
        shown = query.region in provider.regions  # an item outside the provider's regions isn't shown (§4)
        rows, next_cursor = [], None
        if shown:
            if since is not None and key is None:
                with self.lock:
                    deleted = [r["id"] for r in self.db.execute(
                        "SELECT id FROM tombstones WHERE provider = ? AND type = ? AND deleted_at > ? ORDER BY id",
                        (provider.id, contract_type, since))]
            if since is None or since < state["token"]:
                rows, next_cursor = self.page(provider.id, contract_type, query, since, section, key, token, state)
        items = [i for i in (json.loads(r) for r in rows) if contracts.current(i, query.now)]
        return {"provider": provider.id, "type": contract_type, "version": contracts.version(contract_type),
                "region": query.region, "generated_at": contracts.iso(query.now), "ttl": contracts.ttl(contract_type),
                "items": items, "deleted": deleted if key is None else [], "dropped": state["dropped"],
                "next_cursor": next_cursor, "sync_token": token}

    def page(self, pid, contract_type, query, since, section, key, token, state):
        """([json], next_cursor): one page. A `since` read is one keyset over (updated_at, id); a full read of
        availability is two sections, the offers and channels by (updated_at, id) then the window's airings
        by (start, id); any other full read is one section."""
        if since is not None:
            sections = [("changed", since)]
        elif contract_type == "availability":
            sections = [("rest", None), ("airings", None)]
        else:
            sections = [("all", None)]
        out, want = [], query.limit + 1
        while section < len(sections) and len(out) < want:
            name, floor = sections[section]
            sql, params = self.select(pid, contract_type, query, name, floor, key, want - len(out), state)
            with self.lock:
                got = self.db.execute(sql, params).fetchall()
            out += [(section, (row[0], row[1]), row[2]) for row in got]
            if len(out) < want:
                section, key = section + 1, None
        more = len(out) > query.limit
        out = out[:query.limit]
        if not more or not out:
            return [r[2] for r in out], None
        last = out[-1]
        return [r[2] for r in out], cursor_out(last[0], last[1], token)

    def select(self, pid, contract_type, query, section, since, key, limit, state):
        """(sql, params) for one section of one page, ordered by its key, `limit` rows from `key` on."""
        terms = []  # (clause, params), in SQL order; the driver is the table the query loops over first
        driver = None
        kinds = sorted(query.kinds)
        if section == "airings":
            if kinds and AIRING not in kinds:
                return "SELECT 0 WHERE 0", []
            kinds = []
        elif section == "rest":
            kinds = [k for k in kinds if k != AIRING] if kinds else kinds
            if query.kinds and not kinds:
                return "SELECT 0 WHERE 0", []
            if not kinds:
                terms.append(("i.kind != ?", [AIRING]))
        if len(kinds) == 1:
            terms.append(("i.kind = ?", kinds))  # one kind: the kind index is already in (updated_at, id) order
        elif kinds:
            terms.append(("+i.kind IN (%s)" % placeholders(kinds), kinds))
        if since is not None:
            terms.append(("i.updated_at > ?", [since]))
        if query.subcategories:
            tags = sorted(query.subcategories)
            terms.append(("EXISTS (SELECT 1 FROM tags t WHERE t.provider = i.provider AND t.type = i.type AND t.item = i.ref "
                          "AND t.tag IN (%s))" % placeholders(tags), tags))
        if query.ids:
            ids = sorted(query.ids)
            driver = ("(SELECT DISTINCT item AS ref FROM keys WHERE provider = ? AND type = ? AND key IN (%s)) d"
                      % placeholders(ids), [pid, contract_type] + ids)
        if query.q:
            expr = match_expr(query.q)
            if not expr:
                return "SELECT 0 WHERE 0", []
            if driver is None and section != "airings":
                driver = ("(SELECT rowid AS ref FROM search WHERE search MATCH ?) d", [expr])
            else:
                terms.append(("i.ref IN (SELECT rowid FROM search WHERE search MATCH ?)", [expr]))
        window = None
        if contract_type == "availability" and query.start is not None:
            clause = "a.start < ? AND a.start > ? AND a.end > ?"
            params = [query.end, query.start - max(state["longest"], 1), query.start]
            if query.channels:
                channels = sorted(query.channels)
                clause += " AND a.channel IN (%s)" % placeholders(channels)
                params += channels
            window = (clause, params)
        if section == "airings":
            columns, order, keyset = "a.start, a.item_id, i.json", "a.start, a.item_id", "(a.start, a.item_id) > (?, ?)"
            source = ("airings a CROSS JOIN items i ON i.provider = a.provider AND i.type = ? AND i.id = a.item_id", [contract_type])
            terms = [("a.provider = ?", [pid])] + ([window] if window else []) + terms
            if driver is not None:
                terms.append(("i.ref IN (SELECT ref FROM %s)" % driver[0], driver[1]))
        else:
            columns, order, keyset = "i.updated_at, i.id, i.json", "i.updated_at, i.id", "(i.updated_at, i.id) > (?, ?)"
            source = ("items i", [])
            if driver is not None:
                source = ("%s CROSS JOIN items i ON i.ref = d.ref" % driver[0], driver[1])
            terms = [("i.provider = ? AND i.type = ?", [pid, contract_type])] + terms
            if window and section == "changed":  # an airing that changed is served inside the window only
                terms.append(("(i.kind != ? OR EXISTS (SELECT 1 FROM airings a WHERE a.provider = i.provider AND a.item_id = i.id AND %s))"
                              % window[0], [AIRING] + window[1]))
        if key is not None:
            terms.append((keyset, list(key)))
        sql = "SELECT %s FROM %s WHERE %s ORDER BY %s LIMIT ?" % (columns, source[0], " AND ".join(t[0] for t in terms), order)
        return sql, source[1] + [p for t in terms for p in t[1]] + [limit]


def attach(providers, store):
    """Every provider in `providers` that `store` holds answers from it from now on; returns the ids attached."""
    found = []
    for provider in providers:
        if store.synced(provider.id):
            provider.store = store
            found.append(provider.id)
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m contracts.store", description="Sync providers into the store (docs/contract/STORE.md).")
    sub = parser.add_subparsers(dest="command")
    sync = sub.add_parser("sync", help="read every feed type of each provider whole and write the store")
    sync.add_argument("provider", nargs="+", help="provider ids (contracts/<id>.py)")
    sync.add_argument("--db", default=DEFAULT_DB, help="the SQLite file (default %s)" % DEFAULT_DB)
    args = parser.parse_args(argv)
    if args.command != "sync":
        parser.print_help()
        return 2
    from contracts.serve import load
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    failed = 0
    with Store(args.db) as store:
        for pid in args.provider:
            try:
                provider = load(pid)
            except (ImportError, LookupError) as error:
                print("%s: %s" % (pid, error))
                failed += 1
                continue
            for contract_type, done in store.sync(provider).items():
                print("%s %s: %d items (+%d ~%d -%d), dropped %d, token %s" % (
                    pid, contract_type, done["items"], done["added"], done["changed"], done["removed"], done["dropped"], done["token"]))
                if done["dropped"]:
                    failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
