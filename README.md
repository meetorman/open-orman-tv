# open-orman-tv

The Orman TV provider contract, its SDK, and the conformance kit. Run your own provider; the app reads it like any other.

- **PROVIDERS.md**: the rules. One page. It does not change when a provider is added.
- `contracts/`: SDK, schemas, one reference provider. Coming in the first PR (#1).
- `python3 -m contracts.conform <id>`: is my provider valid?

## Working here

- `develop` is where work lands. `main` is what is released. PRs go `develop` to `main`.
- Issues are short: what, done-when, out. Labels say what kind it is: `contract`, `kit`, `provider`, `documentation`, `bug`. `decision` means it needs a call. `blocked` says why it waits.
- A PR says what changed and how it was proven, in a few lines.
