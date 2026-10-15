# open-orman-tv

The Orman TV provider contract, its SDK, and the conformance kit. Run your own provider; the app reads it like any other.

## Map

| Path | What |
|---|---|
| `docs/contract/PROVIDERS.md` | The rules. One page. Does not change when a provider is added. |
| `docs/contract/` | Types, envelopes, versioning, conformance. |
| `docs/decisions/` | Why the contract is the way it is. One file per decision. |
| `docs/guides/` | How to build a provider, how to run a source. |
| `contracts/` | SDK, schemas, reference provider, conformance kit. Lands with #1. |
| `GOVERNANCE.md` | How this repo decides and ships. |
| `CHANGELOG.md` | One line per change to the contract. |

## Quick start

```
python3 -m contracts.conform <id>
```

Green means the app can read your provider. Red lines say what to fix.
