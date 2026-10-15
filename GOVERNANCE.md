# Governance

## Branches

- `develop`: where work lands. Default branch.
- `main`: what is released. Only PRs from `develop`. Empty until the first release.

## Issues

Short. Three parts: **what**, **done when**, **out**. Use the templates.

| Label | Means |
|---|---|
| `contract` | The spec or the schemas |
| `kit` | SDK or conformance kit |
| `provider` | A reference provider |
| `documentation` | Docs only |
| `bug` | Something is wrong |
| `decision` | Needs Bryan's call before work starts |
| `blocked` | Waiting on something; the issue says what |
| `good first issue` | Small and self-contained |

## Pull requests

A few lines: what changed, how it was proven. One reviewer. Squash-merge.

## Changing the contract

| Change | Needs |
|---|---|
| New provider, kind, optional field, enum value, subcategory | A PR. One line in `CHANGELOG.md`. |
| Required field added, anything removed, envelope renamed | A `decision` issue, a decision record in `docs/decisions/`, a version bump. |
| The ten invariants | Same as above. If a new provider forces this, the invariants were wrong. |

## Decisions

One file per decision in `docs/decisions/`, numbered. Context, decision, consequences. Under a page.
