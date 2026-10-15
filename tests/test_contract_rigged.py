"""A provider outside the package on the contract: the kit run by module path (tests/rigged.py), offline.
python3 -m unittest tests.test_contract_rigged"""
import logging
import unittest

from contracts import conform

logging.getLogger("orman-contracts").setLevel(logging.ERROR)


class ConformanceTests(unittest.TestCase):
    def test_the_kit_passes_by_module_path(self):
        checks = conform.run("tests.rigged", offline=True)
        self.assertEqual([c.name for c in checks], list(conform.ORDER))
        failed = [c.line() for c in checks if not c.ok]
        self.assertEqual(failed, [], "\n" + "\n".join(failed))
        self.assertIn("rig tests.rigged, the rig builds from the provider's own data, test_contract_rigged.py",
                      checks[-1].detail)


if __name__ == "__main__":
    unittest.main()
