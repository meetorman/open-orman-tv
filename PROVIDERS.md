# Building a provider

The governing rules for every Orman TV provider. Decided on meetorman/orman-tv#141 (2026-10-14) from
`docs/provider-contracts.md`, which holds the detail. This page holds only what does not change: a new provider,
kind, field or subcategory never edits it. If it has to change, the contract changed, and that takes a major
version (§6) and a decision on an issue.

## 1. Words

| Word | Meaning | Example |
|---|---|---|
| **Source** | A server that speaks `/v1`. A member adds it by URL. The store is identity only and grants nothing. | `tv.askorman.com` |
| **Provider** | A named feed on a source. Granted per member by the source's owner. Ungranted means invisible. | `freemovies`, `sd` |
| **Type** | What a provider serves. Feed types: `metadata`, `availability`, `content`. Member types: `reminders`, `storage`, `tvcontrol`. | `availability` |
| **Kind** | An item's shape within a type, named by a JSON Schema file. | `airing`, `series` |
| **Subcategory** | What an item is about. A slug in a field, never a type. | `news`, `movies` |

## 2. The ten invariants

Every provider obeys these. They change only with a major version.

1. **Declaration.** A provider declares `id`, `name`, `types`, `subcategories`, `regions`, `trust`, `attribution`,
   `public` and `delta`. The declared `types` are its only promise.
2. **Three feed types.** `metadata` (what works and channels are), `availability` (who has what, and when),
   `content` (what a member can watch). Nothing else is ever a feed type.
3. **Routes.** `GET /v1/p` lists the member's view. `GET /v1/p/{provider}/{type}` reads a type.
   `GET /v1/p/-/{type}?q=|ids=` looks up across the view.
4. **Feed envelope.** `provider, type, version, region, generated_at, ttl, items, deleted, dropped, next_cursor,
   sync_token`. A stale `since` or cursor answers 410. A closed window is not served.
5. **Item.** `id` is `<provider>:<local id>` and stable for life. `kind` and `updated_at` are required. Times are
   ISO 8601 UTC, durations seconds. Fields the shape does not name are cut by the source and ignored by the app.
   An unknown kind is skipped, never an error.
6. **Shape.** `kind` picks a schema file. An item that fails it is dropped, counted in `dropped`, logged, and never
   served.
7. **Metadata only.** No playable link anywhere except the keys a content shape names in `x-playable`. Streams
   are https HLS or MP4.
8. **Identity.** Strong ids (`imdb`, `wikidata`, `tmdb`, `eidr`, station ids) merge; a conflict never merges.
   `title_id` and `work_id` pair only within one provider. Play appears only where an open offer and a content
   item of the same `service` meet on one work.
9. **Licence.** Attribution is per provider, overridable per item. Art without `license` is not shown. A
   `display: item` credit follows the field it won. No third-party logos.
10. **Access and version.** An ungranted provider is 404 on every route including `/v1/play`, indistinguishable
    from none. `version` is a major.

## 3. What grows without touching this page

| Want | Do | Version |
|---|---|---|
| A new provider | One class, one declaration. Its group is configuration (#137). | none |
| A new kind | One line in `<type>.schema.json` under `x-kinds`, plus a shape file when none fits. | none |
| A new optional field or enum value | Edit the shape file. One changelog line. | none |
| A new subcategory | Use the slug. The vocabulary is a pattern, not a list. | none |
| A new id scheme | A key under `ids`. | none |
| A new type | A `<type>.schema.json` and a module. Apps ignore unknown types. | none |

The core-owned enums (`access`, `license.code`) grow by one changelog line per value. The app treats an unknown
value as "no Play".

## 4. Member types

`reminders`, `storage` and `tvcontrol` are the same family under a second envelope: `provider, type, version,
generated_at, items, next_cursor`. They share the declaration, the grant and 404 rule, the id rule, the schemas
and account-deletion purge. They have no `since`, `region` or `ttl`. Each type's verbs are an annex
(`docs/tv-control-contract.md` §7 and the type's section of the contract), pointed to and never repeated here.

## 5. Build one

1. **Declare.** Subclass `contracts.Provider` in `channel-browser/contracts/<id>.py`. Fill the declaration.
2. **Serve.** One method per declared type, returning contract-shaped items from what the provider already
   holds. Within budget: a page or lookup never waits on a fetch; an on-request fetch is capped and never blocks.
3. **Identify.** Emit every strong id you have. Without them the merge falls back to title matching.
4. **License.** Declare attribution. Give every art a `license`. Link out, never rehost, unless the licence allows.
5. **Prove.** A fixture under `contracts/fixtures/`, a `test_contract_<id>.py`, and a green `contracts.conform`.

## 6. Definition of done

A provider is granted to anyone only when `python3 -m contracts.conform <id>` passes:

- The declaration validates: id pattern, known types, ISO regions, attribution shape, `delta` set.
- A full read of every declared type: `dropped` is 0, every id carries the prefix, `next_cursor` terminates,
  every page inside the budget. A small-limit walk yields the same id set as one big read.
- Delta: with `delta: true`, a `since` read after a change returns the change and its removal. With `false`,
  and for any foreign token, 410.
- No playable link outside `x-playable` keys. Each stream URL answers a HEAD without headers.
- Every airing's `work_id` resolves in the same provider's metadata.
- Every content item pairs with an offer of the same `service` and a work, by strong id or `title_id`.
- Every art has `license`. A `display: item` provider credits on every item.
- `ids=` with one own id and one canonical id, and `q=` with a title prefix, each return the item.
- An ungranted member is absent from `/v1/p` and gets 404 on every route and on `/v1/play`. A granted member
  sees it in `/v1/p`.

## 7. Versioning

| Change | Bump |
|---|---|
| Kind, optional field, enum value, type added | none (one changelog line for a field or value) |
| Required field added, or anything removed | that type's `x-version` |
| Envelope field added | none |
| Envelope field renamed or removed | every type, and `orman_tv: 2` |

The app refuses a newer major and ignores an unknown type.

## 8. Where the detail lives

- The contract: `docs/provider-contracts.md` (v1).
- The shapes: `channel-browser/contracts/schemas/<type>.schema.json` and `<type>.<shape>.schema.json`.
- The SDK: `channel-browser/contracts/__init__.py`. Reference providers: `freemovies.py`, `pdfilms.py`.
- Groups and grants: #137, orman-apple#68. Boundaries: #114. Open-sourcing: #126.
