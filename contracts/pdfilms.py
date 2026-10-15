"""Public domain films: the first content provider (docs/contract/TYPES.md). It serves both the
service's availability ("Public domain films has Detour, always": availability, kind offer) and its content (the
archive.org MP4: content, kind movie). Play shows where the two meet for a title.

A small, hand-checked list of feature films from the Internet Archive that a member can watch in the app. A film is
on the list only when both say it is public domain in the US:
  - the Internet Archive item's own licence (`licenseurl`: a Creative Commons public-domain mark or dedication), and
  - Wikidata's copyright status (P6216 = Q19652, public domain), the same film a films metadata provider lists.
Each item carries the film's Wikidata and IMDb ids, so the app pairs it with the metadata it already shows, and
streams from archive.org's own MP4 files (nothing is rehosted). The list is a proof of the contract, not a
catalogue: about a dozen films, the ones a films metadata provider also lists.

Check the list again (network; prints one line per film and exits non-zero on a problem):
    python3 -m contracts.pdfilms
"""
from pathlib import Path

import contracts

ID = "pdfilms"
LICENSES = {  # the Internet Archive's licenseurl -> (code, name, link)
    "http://creativecommons.org/licenses/publicdomain/":
        ("pd", "Public domain", "https://creativecommons.org/publicdomain/mark/1.0/"),
    "http://creativecommons.org/publicdomain/mark/1.0/":
        ("pd", "Public Domain Mark 1.0", "https://creativecommons.org/publicdomain/mark/1.0/"),
    "http://creativecommons.org/publicdomain/zero/1.0/":
        ("cc0", "CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
}
CHECKED = "2026-10-14T07:00:00Z"  # when FILMS was last checked against the Internet Archive and Wikidata
PD = "http://creativecommons.org/licenses/publicdomain/"
PDM = "http://creativecommons.org/publicdomain/mark/1.0/"
# (wikidata, imdb, title, year, archive.org item, file, seconds, height, bytes, the item's licenseurl)
FILMS = [
    ("Q2062566", "tt0037638", "Detour", 1945, "Detour", "Detour.mp4", 4058, 480, 423770341, PD),
    ("Q623051", "tt0063350", "Night of the Living Dead", 1968, "Night.Of.The.Living.Dead_1080p",
     "NightOfTheLivingDead_1080p.mp4", 5752, 480, 597585042, PD),
    ("Q153886", "tt0010323", "The Cabinet of Dr. Caligari", 1920, "DasKabinettdesDoktorCaligariTheCabinetofDrCaligari",
     "The_Cabinet_of_Dr._Caligari_512kb.mp4", 3061, 240, 222148532, PD),
    ("Q1751461", "tt0023694", "White Zombie", 1932, "White_Zombie_1932", "White_Zombie.mp4", 4004, 480, 416125069, PDM),
    ("Q23892", "tt0041098", "Africa Screams", 1950, "AfricaScreams", "Africa_Screams.mp4", 4735, 480, 492202154, PD),
    ("Q129049", "tt0039324", "Dishonored Lady", 1947, "dishonored_lady", "dishonored_lady.mp4", 5040, 480, 523810466, PD),
    ("Q2076723", "tt0054462", "The Wasp Woman", 1959, "The_Wasp_Women", "The_Wasp_Women.mp4", 4352, 480, 453286426, PD),
    ("Q2389472", "tt0052169", "The Screaming Skull", 1958, "TheScreamingSkull", "TheScreamingSkull.mp4", 4041, 480,
     422746552, PD),
    ("Q2567687", "tt0052655", "A Bucket of Blood", 1959, "ABucketOfBlood1959", "A_Bucket_Of_Blood.mp4", 3893, 480,
     404698635, PD),
    ("Q691828", "tt0060666", "Manos: The Hands of Fate", 1966, "ManosTheHandsofFate", "Manos-TheHandsofFate_512kb.mp4",
     4123, 240, 314163570, PDM),
    ("Q971265", "tt0038988", "The Strange Love of Martha Ivers", 1946, "Martha_Ivers", "Martha_Ivers.mp4", 6952, 480,
     724723115, PD),
    ("Q1066714", "tt0032599", "His Girl Friday", 1940, "his_girl_friday", "his_girl_friday.mp4", 5504, 480,
     575274912, PD),
]
# Each film's trailer: two minutes of its own stream from a scene with people in it, the moments a
# preview reel uses, played in the app with sound. Nothing is cut or rehosted.
TRAILERS = {"Q2062566": 2252, "Q623051": 1294, "Q153886": 2035, "Q1751461": 1341, "Q23892": 2888, "Q129049": 2242,
            "Q2076723": 1218, "Q2389472": 686, "Q2567687": 2374, "Q691828": 927, "Q971265": 3093, "Q1066714": 1238}
TRAILER_S = 120


def download(item, name):
    return "https://archive.org/download/%s/%s" % (item, name)


class PublicDomainFilms(contracts.Provider):
    id = ID
    name = "Public domain films"
    types = ("availability", "content")
    subcategories = ("movies",)
    regions = ("US",)  # public domain in the US; elsewhere a film's status can differ
    trust = 30
    public = True
    delta = False  # a hand-checked list that changes only with a release: no delta from the list itself (a dozen items);
                   # synced into the store (contracts/store.py) it serves one like any stored provider
    attribution = {"text": "Films from the Internet Archive, public domain in the US",
                   "link": "https://archive.org/details/feature_films", "license": "Public domain",
                   "license_link": "https://creativecommons.org/publicdomain/mark/1.0/", "display": "item"}

    def __init__(self, films=None):
        self.films = list(FILMS if films is None else films)

    def changed_at(self, contract_type):
        return contracts.parse_time(CHECKED)

    def availability(self, query):
        """The service's catalogue: Public domain films has each film, always (a file has no window end)."""
        return [{"id": "%s:%s" % (ID, qid), "kind": "offer", "ids": {"wikidata": qid, "imdb": imdb}, "title": title,
                 "updated_at": CHECKED, "subcategories": ["movies"], "window": {"end": None},
                 "service": ID, "service_name": self.name, "region": "US", "access": "free"}
                for qid, imdb, title, *_ in self.films]

    def content(self, query):
        """What this provider can deliver of the service: each film's archive.org MP4, to play in the app."""
        out = []
        for qid, imdb, title, year, item, name, seconds, height, size, licence in self.films:
            code, label, link = LICENSES[licence]
            out.append({"id": "%s:%s" % (ID, qid), "kind": "movie", "service": ID, "title": title, "year": year,
                        "ids": {"wikidata": qid, "imdb": imdb}, "updated_at": CHECKED, "duration": seconds,
                        "subcategories": ["movies"], "access": "free", "regions": list(self.regions),
                        "window": {"end": None}, "how": "play",  # a file: always, open-ended
                        "streams": [{"url": download(item, name), "format": "mp4", "height": height, "bytes": size}],
                        "license": {"code": code, "name": label, "link": link,
                                    "source": "https://archive.org/details/" + item, "credit": "Internet Archive"}})
            if qid in TRAILERS:
                out[-1]["previews"] = [{"start": TRAILERS[qid], "duration": min(TRAILER_S, seconds - TRAILERS[qid]),
                                        "label": "Trailer"}]
        return out


def rig(directory, now):
    """The conformance rig (contracts.conform): the provider as it is, its list its fixture, synced into a store in
    `directory` so the kit reads it as the reference source serves it. `change()` adds a film and removes the first,
    then syncs again, so the delta check has a change and a removal to look for."""
    from contracts.store import Store

    provider = PublicDomainFilms()
    store = Store(str(Path(directory) / "pdfilms.db"))
    provider.sync(store, now)

    def change():
        first = provider.films[0][0]
        added = ("Q0", "tt0000000", "A film added by the kit", 1950, "kit", "kit.mp4", 600, 240, 1, PD)
        provider.films = [added] + provider.films[1:]
        provider.sync(store, now + 1)
        return {t: (["%s:Q0" % ID], ["%s:%s" % (ID, first)]) for t in provider.types}

    return provider, change


def verify():
    """[problem] for every film whose archive.org item, file or licence, or Wikidata status, no longer holds."""
    import json
    import urllib.request

    def get(url):
        req = urllib.request.Request(url, headers={"User-Agent": "open-orman-tv pdfilms check", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)

    problems = []
    for qid, imdb, title, year, item, name, seconds, height, size, licence in FILMS:
        meta = get("https://archive.org/metadata/" + item)
        files = {f["name"]: f for f in meta.get("files") or []}
        found = (meta.get("metadata") or {}).get("licenseurl")
        claims = get("https://www.wikidata.org/wiki/Special:EntityData/%s.json" % qid)["entities"][qid]["claims"]
        status = [c["mainsnak"].get("datavalue", {}).get("value", {}).get("id") for c in claims.get("P6216", [])]
        wrong = [what for what, bad in (
            ("archive.org licence is %s" % found, found not in LICENSES or found != licence),
            ("file %s is gone" % name, name not in files),
            ("file size changed", name in files and int(files[name].get("size") or 0) != size),
            ("Wikidata does not say public domain", "Q19652" not in status)) if bad]
        print("%-34s %s" % (title, "; ".join(wrong) or "ok"))
        problems += ["%s: %s" % (title, w) for w in wrong]
    return problems


if __name__ == "__main__":
    raise SystemExit(1 if verify() else 0)
