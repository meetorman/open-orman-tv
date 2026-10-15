# Types

A provider declares the types it serves. Each type has kinds; each kind has one schema file.

## Feed types

Read-only, paged, with `since` deltas. The app merges them on the device.

| Type | Answers | Kinds |
|---|---|---|
| `metadata` | What works and channels **are** | `movie`, `series`, `season`, `episode`, `event`, `channel`, `list` |
| `availability` | Who **has** which works, and **when** | `offer`, `channel`, `airing` |
| `content` | What a member can **watch** | `movie`, `episode`, `live`, `preview` |

## Member types

Written by the member, scoped to the member. Second envelope (`ENVELOPES.md`). No `since`, `region` or `ttl`.
Each type's verbs are its own annex.

| Type | Holds | Kind |
|---|---|---|
| `reminders` | "Remind me when it's on" | `reminder` |
| `storage` | The account's shared state | `document` |
| `tvcontrol` | Registered TVs and their tokens | `controller` |

## Schemas

- `contracts/schemas/<type>.schema.json`: the type. `x-version`, `x-ttl`, and `x-kinds` mapping each kind to a shape.
- `contracts/schemas/<type>.<shape>.schema.json`: the shape. Plain JSON Schema; any validator can check it.
- `x-playable` on a shape names the only keys that may hold a stream. Metadata and availability shapes never have it.

## Subcategories

A slug in `subcategories[]`: `movies`, `tv-shows`, `channels`, `news`, `sports`, `kids`, `documentaries`, `music`, `apps`, and
any other slug matching the pattern in `common.schema.json`. A subcategory is never a type.
