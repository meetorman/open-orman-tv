# Run your own source

A source is your providers behind HTTP. The app adds it by URL. Five steps; a fresh clone serves `pdfilms` after two.

1. **Clone.**

```
git clone https://github.com/meetorman/open-orman-tv.git && cd open-orman-tv
```

2. **Serve.** No config needed: `pdfilms` is served public on port 8080.

```
python3 -m contracts.serve
```

   `http://<your host>:8080/v1/source` answers with the source and its public providers. That is the URL to add in the
   app. Stop here if `pdfilms` is all you want. A big provider is served from the store (`../contract/STORE.md`):
   `python3 -m contracts.serve --db source.db` syncs it at start and reads are indexed, with real `since` deltas.

3. **Add a provider.** Write `contracts/<id>.py` as `build-a-provider.md` says, then prove it:

```
python3 -m contracts.conform <id>
```

4. **Grant it.** Copy `source.example.json` to `source.json`. Name the source, list the providers to load, and give each
   member a bearer token and the provider ids they hold. A public provider is in every view; a private one only in the
   views that name it. No token sees the public providers; a token the file doesn't name is 401.

```
python3 -m contracts.serve --config source.json --port 8080
```

5. **Check it over the wire.** The same ten checks, read from the running server:

```
python3 -m contracts.conform <id> --url http://127.0.0.1:8080 --token <a member's token>
```

   A member whose token holds the provider enters it in the app as `Authorization: Bearer <token>`.

## What this is not

No TLS, no sessions, no rate limit, no deployment. Keep it on your LAN, or put a reverse proxy in front of it that
terminates TLS. Production auth is the host's job; this is the smallest grant model that lets the app read a source.
