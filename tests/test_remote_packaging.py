import unittest
from pathlib import Path


class RemotePackagingTests(unittest.TestCase):
    def test_examples_are_isolated(self):
        root = Path(__file__).parents[1] / "remote_control"
        service = (root / "control-server.service.example").read_text(encoding="utf-8")
        compose = (root / "docker-compose.example.yml").read_text(encoding="utf-8")
        self.assertIn("maa-control", service); self.assertIn("18765", service); self.assertIn("256M", service)
        self.assertIn("maa-control", compose); self.assertIn("127.0.0.1:18765:8765", compose); self.assertIn("mem_limit: 256m", compose)


if __name__ == "__main__": unittest.main()
