# Providers: the rules

The governing page for every Orman TV provider. Decided 2026-10-14 (decision 0001). It holds only what does not
change: a new provider, kind, field or subcategory never edits it. If it has to change, the contract changed, and
that takes a decision and a major version.

## Words

| Word | Meaning | Example |
|---|---|---|
| **Source** | A server that speaks `/v1`. A member adds it by URL. Identity lives elsewhere; a source grants its own providers. | `tv.askorman.com` |
| **Provider** | A named feed on a source. Granted per member by the source's owner. Ungranted means invisible. | `pdfilms` |
| **Type** | What a provider serves. Feed: `metadata`, `availability`, `content`. Member: `reminders`, `storage`, `tvcontrol`. | `availability` |
| **Kind** | An item's shape within a type, named by a JSON Schema file. | `airing`, `series` |
| **Subcategory** | What an item is about. A slug in a field, never a type. | `news`, `movies` |

## The ten invariants

1. **Declaration.** A provider declares `id`, `name`, `types`, `subcategories`, `regions`, `trust`, `attribution`,
   `public` and `delta`. The declared `types` are its only promise.
2. **Three feed types.** `metadata` (what works and channels are), `availability` (who has what, and when),
   `content` (what a member can watch). Nothing else is ever a feed type.
3. **Routes.** `GET /v1/p` lists the member's view. `GET /v1/p/{provider}/{type}` reads a type.
   `GET /v1/p/-/{type}?q=|ids=` looks up across the view.
4. **Envelope.** Fixed keys (`ENVELOPES.md`). A stale `since` or cursor answers 410. A closed window is not served.
5. **Item.** `id` is `<provider>:<local id>` and stable for life. `kind` and `updated_at` are required. Times are
   ISO 8601 UTC, durations seconds. Unknown fields are cut by the source and ignored by the app. An unknown kind
   is skipped, never an error.
6. **Shape.** `kind` picks a schema file. An item that fails it is dropped, counted in `dropped`, logged, never
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

## Next

- What grows without touching this page: `VERSIONING.md`.
- The checks a provider must pass: `CONFORMANCE.md`.
- Build one: `../guides/build-a-provider.md`.
