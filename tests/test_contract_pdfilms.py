"""pdfilms on the provider contract: the open conformance kit (contracts/conform.py, docs/contract/PROVIDERS.md §6) run against
the provider's rig, offline (no HEAD of its stream URLs). The provider's own behaviour is tested in test_contracts.
python3 -m unittest test_contract_pdfilms"""
import logging
import unittest

from contracts import conform

logging.getLogger("orman-contracts").setLevel(logging.ERROR)


class ConformanceTests(unittest.TestCase):
    def test_the_kit_passes(self):
        checks = conform.run("pdfilms", offline=True)
        self.assertEqual([c.name for c in checks], list(conform.ORDER))
        failed = [c.line("pdfilms") for c in checks if not c.ok]
        self.assertEqual(failed, [], "\n" + "\n".join(failed))


if __name__ == "__main__":
    unittest.main()
