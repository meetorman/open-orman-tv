# 0001. Contract v1

2026-10-14. Status: accepted.

## Context

The contract had been proposed for a while and kept moving: new kinds, enums and fields landed per provider, the
version never bumped, and the member types each faked a different envelope. Two positions were argued: freeze
the smallest core so the rules never change, and name what will actually break. They agreed on most of it.
Where they differed, the smallest rule that prevents a known breakage won.

## Decision

- **Ten invariants** are the contract (`../contract/PROVIDERS.md`). They change only with a major version.
- **Three feed types** (metadata, availability, content). Series is a kind, never a type. Subcategories are a
  field, never a type.
- **Member types** (reminders, storage, tvcontrol) are the same family under a second, smaller envelope. Their
  verbs are annexes.
- **Extension without a bump**: provider, kind, optional field, enum value, subcategory, id scheme, type
  (`../contract/VERSIONING.md`).
- **Added to prevent known breakage**: `dropped` in the envelope (a 200 with an empty app is a failure);
  `delta` in the declaration (never promise `since` and not keep it); a budget on pages and lookups (an
  on-request fetch never blocks the answer); pairing by strong id, else `title_id` within one provider.
- **Merge runs on the device.** It is the app's, not the contract's. The cross-view lookup stays as the fallback.
- **Trust** orders providers within one source; across sources the member's source order wins first.
- **Deferred with a default**: third-party metadata services stay a private, source-side dependency; channel
  trending is a private metadata list; region is US, declared and carried, filtered by the app.
- **Definition of done**: `python3 -m contracts.conform <id>` (`../contract/CONFORMANCE.md`).

## Consequences

- A provider that didn't exist yesterday needs no doc change today. If it does, an invariant was wrong, and that
  is a new decision, not an edit.
- The conformance kit is the open test of the contract. It ships first (#1).
- Private providers stay out of this repo; they obey the same rules.
