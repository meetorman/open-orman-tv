"""A provider kept outside the contracts package, the way a source keeps its own: the kit finds it by module path
(`python3 -m contracts.conform --providers tests.rigged`), `serve` loads it the same way (`"providers":
["tests.rigged"]`), and its proof lives beside it (tests/test_contract_rigged.py)."""
import time

import contracts


class Rigged(contracts.Provider):
    id, name = "rigged", "Rigged"
    types = ("metadata",)
    subcategories = ("movies",)
    regions, trust, public, delta = ("US",), 50, True, True
    attribution = {"text": "Made up", "display": "about"}

    def __init__(self, now=None):
        self.changed = int(now or time.time())
        self.works = {n: self.work(n, self.changed) for n in range(1, 4)}
        self.removed = []

    def work(self, n, at):
        return {"id": "rigged:w%d" % n, "kind": "movie", "title": "Film %d" % n, "year": 1950 + n,
                "ids": {"wikidata": "Q%d" % n}, "updated_at": contracts.iso(at)}

    def changed_at(self, contract_type):
        return self.changed

    def removed_since(self, contract_type, since):
        return list(self.removed)

    def metadata(self, query):
        return list(self.works.values())


def rig(directory, now):
    """The conformance rig: the provider as it is, and change() for the delta check (one addition, one removal)."""
    provider = Rigged(now)

    def change():
        provider.changed += 60
        provider.works[9] = provider.work(9, provider.changed)
        del provider.works[1]
        provider.removed = ["rigged:w1"]
        return {"metadata": (["rigged:w9"], ["rigged:w1"])}

    return provider, change
