# Build a provider

Five steps. Read `../contract/PROVIDERS.md` first; it is one page.

1. **Declare.** Subclass `contracts.Provider` in `contracts/<id>.py`. Fill `id`, `name`, `types`, `subcategories`,
   `regions`, `trust`, `attribution`, `public`, `delta`.
2. **Serve.** One method per declared type, returning contract-shaped items from what you already hold. A page or
   lookup never waits on a fetch; an on-request fetch is capped and never blocks the answer.
3. **Identify.** Emit every strong id you have (`imdb`, `wikidata`, `tmdb`, `eidr`, station ids). Without them the
   merge falls back to title matching.
4. **License.** Declare attribution. Give every art a `license`. Link out; rehost only when the licence allows.
5. **Prove.** A fixture under `contracts/fixtures/`, a `test_contract_<id>.py`, and:

```
python3 -m contracts.conform <id>
```

Green means done. The reference provider `contracts/pdfilms.py` is the smallest example that passes.
