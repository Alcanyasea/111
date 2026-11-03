import unittest
import time

from remote_control.protocol import ProtocolError, command_payload, redact_config, redact_status


class RemoteProtocolTests(unittest.TestCase):
    def test_valid_command_payload(self):
        payload = command_payload("run", {}, "abc", int(time.time()) + 60)
        self.assertEqual(payload["kind"], "run")
        self.assertEqual(payload["args"], {})

    def test_command_arguments_are_kind_specific(self):
        with self.assertRaises(ProtocolError): command_payload("stop", {"path": "../../x"}, "x", 123)
        with self.assertRaises(ProtocolError): command_payload("switch_account", {"slot": "bad slot"}, "x", 123)
        payload = command_payload("patch_config", {"base_revision": 1, "patch": {"schedule": {}}}, "x", int(time.time()) + 60)
        self.assertEqual(payload["args"]["base_revision"], 1)

    def test_rejects_unknown_or_shell_arguments(self):
        with self.assertRaises(ProtocolError):
            command_payload("exec", {}, "x", 123)
        with self.assertRaises(ProtocolError):
            command_payload("run", {"command": "pwsh evil.ps1"}, "x", 123)

    def test_redacts_secrets(self):
        cfg = {"accounts": [{"id": "a", "label": "A", "username": "u", "password": "p", "slot": "s"}], "paths": {"log_file": "x"}, "schedule": {"times": []}}
        safe = redact_config(cfg)
        self.assertEqual(safe["accounts"][0], {"id": "a", "label": "A", "server": "official", "enabled": True, "slot_present": False})
        status = redact_status({"log": "secret", "token": "t", "screenshot": "x", "phase": "idle"})
        self.assertEqual(status, {"phase": "idle"})


if __name__ == "__main__":
    unittest.main()
