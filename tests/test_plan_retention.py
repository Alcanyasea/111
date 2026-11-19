"""Cache cleanup must preserve plans still referenced by MAA (temporary data only)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_support import appconfig  # Import config without probing real MAA paths.
from core import cleanup
from tests.ps_harness import run_ps


class PlanRetentionTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.plans = self.root / "plugins" / "base_schedule" / "plans"
        self.plans.mkdir(parents=True)
        self.original = b'{"plans":[{"name":"morning"},{"name":"evening"}]}'
        for name in ("official_1.json", "bilibili_1.json"):
            (self.plans / name).write_bytes(self.original)
        self.cache = self.root / "plugins" / "base_schedule" / "__pycache__"
        self.cache.mkdir()
        (self.cache / "sample.pyc").write_bytes(b"bytecode")
        (self.root / "scripts").mkdir()

    def assert_plans_preserved(self):
        for name in ("official_1.json", "bilibili_1.json"):
            plan = self.plans / name
            self.assertTrue(plan.is_file(), f"Cleanup removed referenced plan: {name}")
            self.assertEqual(plan.read_bytes(), self.original)
        self.assertFalse(self.cache.exists(), "Python cache should still be removed")

    def test_startup_cleanup_preserves_all_accounts_plans(self):
        run_ps(self, self.root, ["scripts/master_lib.ps1"], """
Clear-CacheData
Clear-CacheData
@{done=$true} | ConvertTo-Json
""")
        self.assert_plans_preserved()

    def test_console_cleanup_does_not_offer_or_delete_plans(self):
        with patch.multiple(cleanup, SCRIPT_DIR=self.root / "scripts",
                            LOCK_FILE=self.root / "scripts" / "master.lock",
                            CACHE_ROOTS=(self.root / "gui", self.root / "plugins"),
                            PLANS_DIR=self.plans, BACKUPS=(), create=True), \
                patch.object(cleanup.runner, "stale_lock", return_value=None):
            items = cleanup.scan()
            self.assertFalse(any(Path(it["path"]).suffix == ".json" for it in items),
                             "MAA plans must not be offered as disposable cache")
            freed, ok, failed = cleanup._perform_unlocked(items)
        self.assertEqual((freed, ok, failed), (8, 1, 0))
        self.assert_plans_preserved()


if __name__ == "__main__":
    unittest.main()
