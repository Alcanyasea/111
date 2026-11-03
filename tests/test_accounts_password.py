# -*- coding: utf-8 -*-
"""账号捕获对话框的密码字段：DPAPI 密文显示为明文，解不开时不覆盖原值。

offscreen + 临时目录；appconfig.save 被 patch，不写真实 config.json。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from core import secret  # noqa: E402
from pages.accounts import CaptureDialog  # noqa: E402


@unittest.skipUnless(secret.available(), "Windows DPAPI required")
class AccountPasswordFieldTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = {"paths": {"script_dir": str(self.root)},
                    "accounts": [], "schedule": {"times": [], "collect_times": []}}

    def _dialog(self, acc):
        dlg = CaptureDialog(self.cfg, acc, page=None, parent=None)
        self.addCleanup(dlg.deleteLater)
        return dlg

    def _account(self, password):
        return {"id": "acc_x", "label": "甲", "server": "official",
                "enabled": True, "slot": "acc_x", "username": "u",
                "password": password, "second_fight_plan": [],
                "second_fight_use_optional": True}

    def test_encrypted_password_is_shown_decrypted(self):
        dlg = self._dialog(self._account(secret.protect("pw-密-123")))
        self.assertEqual(dlg.pass_edit.text(), "pw-密-123")
        self.assertFalse(dlg._password_broken)

    def test_legacy_plaintext_password_is_shown_as_is(self):
        dlg = self._dialog(self._account("legacy-pass"))
        self.assertEqual(dlg.pass_edit.text(), "legacy-pass")
        self.assertFalse(dlg._password_broken)

    def test_broken_cipher_is_empty_and_kept_on_save(self):
        broken = "dpapi:AAAA"
        acc = self._account(broken)
        dlg = self._dialog(acc)
        self.assertTrue(dlg._password_broken)
        self.assertEqual(dlg.pass_edit.text(), "")
        self.assertIn("无法解密", dlg.hint_label.text())
        dlg._slot = "acc_x"
        with patch("pages.accounts.appconfig.save") as save:
            dlg._apply_result()
        save.assert_called_once()
        # 用户没重填 → 原密文保留，不能被空串覆盖
        self.assertEqual(acc["password"], broken)

    def test_retrieved_password_replaces_broken_cipher(self):
        acc = self._account("dpapi:AAAA")
        dlg = self._dialog(acc)
        dlg.pass_edit.setText("new-pass")
        dlg._slot = "acc_x"
        with patch("pages.accounts.appconfig.save"):
            dlg._apply_result()
        self.assertEqual(acc["password"], "new-pass")


if __name__ == "__main__":
    unittest.main()
