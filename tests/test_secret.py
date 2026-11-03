# -*- coding: utf-8 -*-
"""账号密码 DPAPI 加解密：Python 与 PowerShell 两侧必须互认。

全部使用临时数据，不读写真实 config.json 或账号槽位。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from core import secret  # noqa: E402
from tests.ps_harness import run_ps  # noqa: E402


@unittest.skipUnless(secret.available(), "Windows DPAPI required")
class SecretRoundtripTest(unittest.TestCase):
    def test_roundtrip_keeps_special_characters(self):
        for text in ("simple", "密码123", "a%b^c_d+e=f[g]", "x" * 300):
            with self.subTest(text=text[:10]):
                sealed = secret.protect(text)
                self.assertTrue(secret.is_protected(sealed))
                self.assertNotIn(text, sealed)
                self.assertEqual(secret.unprotect(sealed), text)

    def test_plaintext_is_returned_unchanged(self):
        self.assertFalse(secret.is_protected("plain-pass"))
        self.assertEqual(secret.unprotect("plain-pass"), "plain-pass")
        self.assertEqual(secret.unprotect(""), "")

    def test_protect_is_idempotent_and_skips_empty(self):
        sealed = secret.protect("abc")
        self.assertEqual(secret.protect(sealed), sealed)
        self.assertEqual(secret.protect(""), "")

    def test_corrupt_blob_returns_none(self):
        for broken in ("dpapi:", "dpapi:!!!notbase64!!!", "dpapi:AAAA"):
            with self.subTest(broken=broken):
                self.assertIsNone(secret.unprotect(broken))

    def test_non_windows_disables_both_directions(self):
        # 非 Windows 环境（或系统拒绝）不能静默改配置：加密返回 None，调用方保留明文
        with patch.object(secret, "_CRYPT32", None):
            self.assertIsNone(secret.protect("abc"))
            self.assertIsNone(secret.unprotect(secret.PREFIX + "AAAA"))


@unittest.skipUnless(secret.available(), "Windows DPAPI required")
class PowerShellSecretTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_ps_decrypts_blob_written_by_python(self):
        sealed = secret.protect("跨语言-密码-42")
        r = run_ps(self, self.root, ["scripts/secret_lib.ps1"], r'''
$v = Unprotect-DpapiSecret $env:TEST_SECRET
@{ value = $v } | ConvertTo-Json
''', env={"TEST_SECRET": sealed})
        self.assertEqual(r["value"], "跨语言-密码-42")

    def test_plaintext_passthrough_and_broken_blob(self):
        r = run_ps(self, self.root, ["scripts/secret_lib.ps1"], r'''
@{ plain = (Unprotect-DpapiSecret 'old-plain')
   broken = (Unprotect-DpapiSecret 'dpapi:AAAA')
   empty = (Unprotect-DpapiSecret '') } | ConvertTo-Json
''')
        self.assertEqual(r["plain"], "old-plain")
        self.assertIsNone(r["broken"])
        self.assertEqual(r["empty"], "")


if __name__ == "__main__":
    unittest.main()
