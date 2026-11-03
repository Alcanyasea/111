import tempfile
import unittest
from pathlib import Path

from remote_control.agent import AgentClient, CommandExecutor


class FakeResponse:
    def __init__(self, payload, status=200): self.payload, self.status = payload, status
    def read(self):
        import json
        return json.dumps(self.payload).encode()


class FakeOpener:
    def __init__(self): self.calls = []
    def open(self, request, timeout=0):
        self.calls.append(request)
        return FakeResponse({"command": None})


class RemoteAgentTests(unittest.TestCase):
    def test_client_poll_uses_bearer_token(self):
        opener = FakeOpener(); client = AgentClient("http://server", "d1", "t", opener)
        self.assertEqual(client.poll(), {"command": None})
        self.assertEqual(opener.calls[0].headers["Authorization"], "Bearer t")

    def test_executor_refuses_run_when_busy(self):
        class Runner:
            def is_running(self): return True
        executor = CommandExecutor({}, Runner(), None, None)
        result = executor.execute({"payload": {"kind": "run", "args": {}}})
        self.assertEqual(result["state"], "failed")
        self.assertIn("busy", result["error"])

    def test_executor_calls_runner_for_stop(self):
        class Runner:
            def is_running(self): return False
            def stop(self): self.called = True
        runner = Runner(); executor = CommandExecutor({}, runner, None, None)
        result = executor.execute({"payload": {"kind": "stop", "args": {}}})
        self.assertEqual(result["state"], "succeeded"); self.assertTrue(runner.called)


if __name__ == "__main__": unittest.main()
