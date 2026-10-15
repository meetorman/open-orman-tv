# Conformance: the definition of done

A provider is granted to anyone only when this passes:

```
python3 -m contracts.conform <id>
```

One line per check. Non-zero on any failure. `--offline` skips the network check. `--url <source>` runs the same
checks over HTTP against a running source (`docs/guides/run-your-own-source.md`). `--providers <module>[,<module>]`
runs providers kept outside this package, by dotted module path: each module defines `rig(directory, now)` like
`contracts/<id>.py` does, with its `test_contract_<id>.py` and `fixtures/<id>.json` beside it; with `--all` they run
alongside the package's own.

| # | Check |
|---|---|
| 1 | The declaration validates: id pattern, known types, ISO regions, attribution shape, `delta` set. |
| 2 | A full read of every declared type: `dropped` is 0, every id carries the prefix, `next_cursor` terminates, every page inside the budget. A small-limit walk yields the same id set as one big read. |
| 3 | Delta: with `delta: true`, a `since` read after a change returns the change and its removal. With `false`, and for any foreign token, 410. |
| 4 | No playable link outside `x-playable` keys. Each stream URL answers a HEAD without headers. |
| 5 | Every airing's `work_id` resolves in the same provider's metadata. |
| 6 | Every content item pairs with an offer of the same `service` and a work, by strong id or `title_id`. |
| 7 | Every art has `license`. A `display: item` provider credits on every item. |
| 8 | `ids=` with one own id and one canonical id, and `q=` with a title prefix, each return the item. |
| 9 | An ungranted member is absent from `/v1/p` and gets 404 on every route and on `/v1/play`. A granted member sees it in `/v1/p`. |
| 10 | A fixture under `contracts/fixtures/` (or `fixtures/` beside a provider kept outside the package) and a `test_contract_<id>.py`. |

A 200 with an empty app is a failure, not a success.
