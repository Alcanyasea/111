import unittest

from remote_control.protocol import ProtocolError, command_payload, redact_config, redact_status


class RemoteProtocolTests(unittest.TestCase):
    def test_valid_command_payload(self):
        payload = command_payload("run", {"mode": "normal"}, "abc", 1234567890)
        self.assertEqual(payload["kind"], "run")
        self.assertEqual(payload["args"], {"mode": "normal"})

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
