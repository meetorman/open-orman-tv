# The store

A source's providers in SQLite, written once at sync, read as indexed SELECTs. `contracts/store.py`, stdlib only
(sqlite3 in WAL mode, FTS5). The contract does not change: the same routes, envelope and item rules
(`ENVELOPES.md`), whether a provider is served from the store or from its type methods.

## Why

Without it, a read builds a provider's whole list in Python and slices a page, and no provider can serve a `since`
delta. At 100,000 items that is the bottleneck. With the store the server is the database: validation happens at
ingest, a page is one SELECT with a keyset cursor, and a delta is `updated_at > token` plus tombstones.

## Sync

```
python3 -m contracts.store sync <id> [<id> ...] [--db source.db]
```

One line per type: items, how many were added, changed and removed, how many were dropped, the new sync token.
Exit 1 when anything was dropped. `python3 -m contracts.serve --db source.db` syncs every provider at start and
serves from the store; run `sync` again whenever the data changes, the server picks it up on the next read.

At sync the provider's type methods are called once, every item runs through `schema.clean`, and an item that
fails its shape is counted, logged and never stored (`PROVIDERS.md` §6). An unchanged item keeps its row; a changed
or new one is stamped with this sync; an id that vanished gets a tombstone. A closed window is not stored. Reads
never validate. In code, `provider.sync(store)` does the same; a provider that can do better (a delta from its
upstream) overrides it.

The store holds `items`, `airings` (channel, start, end: the window read), `tombstones`, `keys` (what `ids=`
finds), `tags` (what `subcategory=` finds), an FTS5 index over title, name, series and channel name (`q=`, every
word a prefix), and one `syncs` row per provider and type.

## What delta means now

A stored provider declares `delta: true` whatever its class says. `since=<sync_token>` answers every item changed
by a later sync, in `(updated_at, id)` order, and `deleted` names the ids that left. `updated_at` here is the
sync that last changed the row, so a provider that forgets to bump its own `updated_at` is still delivered.

A token from before the first sync, from after the latest, not one of this source's, or older than the tombstones
the store keeps (30 days) is **410**: read everything again. A cursor carries the token it started with and is 410
once a sync moved it. `dropped` is the latest sync's count.

The reference server answers every envelope with a weak ETag, the sync token and the canonical query, so a client
that sends it back as `If-None-Match` gets a 304 until the next sync that changes something.
