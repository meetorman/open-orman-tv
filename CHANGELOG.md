# Changelog

One line per change to the contract. Newest first. Format: `date  type.kind or scope  what  (PR)`.

## Unreleased

- 2026-10-14  kit  `conform --providers` and `serve --providers` / `"providers": ["my_providers.films"]`: providers kept outside the package, by dotted module path; their test and fixture live beside the module
- 2026-10-14  kit  `pyproject.toml`: the SDK installs with pip from a tag or commit (schemas and fixtures included)
- 2026-10-14  kit  reconciled with the first source's copy (#5): a `q=` read rejects non-matching raw items before validation (a large catalogue search validates only candidates); the type fixtures (`contracts/fixtures/<type>.json`) and their schema test

## v1.1.0 (2026-10-15)

- 2026-10-14  kit  `contracts/store.py`: providers synced into SQLite, keyset pages, `since` from `updated_at` and tombstones (stored providers declare `delta: true`), ETag/304 and gzip in `serve` (#9)
- 2026-10-14  kit  `python3 -m contracts.serve`: the reference source over HTTP, `source.json` grants; `conform --url` reads a running source (#7)
- 2026-10-14  kit  `python3 -m contracts.conform <id>`: the ten checks, in-process, `--offline` skips the HEAD check (#1)
- 2026-10-14  envelope  `dropped` added; declaration  `delta` added. Additive, no bump (#1)
- 2026-10-14  contract  v1 decided: ten invariants, feed and member envelopes, versioning, conformance (decision 0001)
