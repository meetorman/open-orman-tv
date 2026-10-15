# Envelopes, items, routes

## Routes

| Route | Answers |
|---|---|
| `GET /v1/source` | The source and its public providers. Open. |
| `GET /v1/p` | The providers in this member's view. |
| `GET /v1/p/{provider}/{type}` | One type of one provider. 404 when not in the view. |
| `GET /v1/p/-/{type}?q=\|ids=` | A lookup across every provider in the view. |

Parameters, all optional: `limit` (default 500, max 2000), `cursor`, `since`, `region`, `ids`, `kind`, `subcategory`,
`q`. Availability also takes `from`, `to`, `channels` (the airing window, at most 7 days).

## Feed envelope

```
provider  type  version  region  generated_at  ttl
items[]   deleted[]  dropped  next_cursor  sync_token
```

- `since` = the last `sync_token`: only what changed, plus `deleted`. Too old: **410**, read everything again.
- `dropped`: items that failed their shape, in this read or, from a store, in the latest sync (`STORE.md`). Conformance fails on any.
- A closed window (an offer that left, an airing that ended) is not served.

## Member envelope

```
provider  type  version  generated_at  items[]  next_cursor
```

Same declaration, grant, 404 rule, id rule and schemas as a feed. No `since`, `region` or `ttl`. Verbs per type.

## Item

| Field | Rule |
|---|---|
| `id` | `<provider>:<local id>`. Stable for life. |
| `kind` | Required. Picks the shape. Unknown kind: skipped. |
| `updated_at` | Required. ISO 8601 UTC. |
| `ids` | Strong ids: `imdb`, `wikidata`, `tmdb`, `eidr`, station ids. Recommended; without them merging falls back to titles. |
| `subcategories[]` | Recommended. |
| `attribution` | Optional. Overrides the provider's for this item. |
| anything else | What the shape names. Unknown fields are cut by the source and ignored by the app. |

Durations in seconds. No playable link outside `x-playable` keys.
