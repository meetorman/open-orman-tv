# Changelog

One line per change to the contract. Newest first. Format: `date  type.kind or scope  what  (PR)`.

## Unreleased

- 2026-10-14  kit  `python3 -m contracts.serve`: the reference source over HTTP, `source.json` grants; `conform --url` reads a running source (#7)
- 2026-10-14  kit  `python3 -m contracts.conform <id>`: the ten checks, in-process, `--offline` skips the HEAD check (#1)
- 2026-10-14  envelope  `dropped` added; declaration  `delta` added. Additive, no bump (#1)
- 2026-10-14  contract  v1 decided: ten invariants, feed and member envelopes, versioning, conformance (decision 0001)
