# open-orman-tv

The Orman TV provider contract, its SDK, and the conformance kit. Run your own provider; the app reads it like any other.

## Map

| Path | What |
|---|---|
| `docs/contract/PROVIDERS.md` | The rules. One page. Does not change when a provider is added. |
| `docs/contract/` | Types, envelopes, versioning, conformance. |
| `docs/decisions/` | Why the contract is the way it is. One file per decision. |
| `docs/guides/` | How to build a provider, how to run a source. |
| `contracts/` | SDK, schemas, reference provider, conformance kit. |
| `contracts/serve.py` | The reference source: your providers over HTTP, `python3 -m contracts.serve`. |
| `contracts/store.py` | The store: providers synced into SQLite, indexed reads and real deltas, `python3 -m contracts.store sync <id>`. |
| `GOVERNANCE.md` | How this repo decides and ships. |
| `CHANGELOG.md` | One line per change to the contract. |

## License

Apache-2.0. See `LICENSE`.

## Quick start

```
python3 -m contracts.conform <id>
```

The SDK installs as a package too, so a source keeps its providers in its own repository and imports `contracts`
(pin a tag or commit):

```
pip install "open-orman-tv @ git+https://github.com/meetorman/open-orman-tv@<ref>"
python3 -m contracts.conform --providers my_providers.films      # your providers, by module path
```

Green means the app can read your provider. Red lines say what to fix.

```
python3 -m contracts.serve
```

Serves `pdfilms` on port 8080; the app adds `http://<host>:8080/v1/source`. See `docs/guides/run-your-own-source.md`.
