import unittest

from remote_control.agent import AgentClient, CommandExecutor, run_iteration


class FakeResponse:
    def __init__(self, payload): self.payload = payload
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

    def test_patch_config_reloads_agent_config(self):
        class State:
            def apply_patch(self, patch, revision): self.called = (patch, revision)
        state = State()
        executor = CommandExecutor({"old": True}, None, None, state,
                                   config_loader=lambda: {"new": True})
        result = executor.execute({"payload": {"kind": "patch_config", "args": {
            "patch": {"schedule": {}}, "base_revision": 1}}})
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(state.called, ({"schedule": {}}, 1))
        self.assertEqual(executor.cfg, {"new": True})

    def test_failed_result_upload_does_not_escape_and_snapshot_is_refreshed(self):
        class Executor:
            def execute(self, command): return {"state": "succeeded", "result": {"kind": "refresh_status"}}
        class State:
            def read_snapshot(self): return {"phase": "idle"}
        class Client:
            def __init__(self): self.posts, self.registers = 0, []
            def post_result(self, *args):
                self.posts += 1
                if self.posts < 3: raise OSError("temporary network error")
            def register(self, snapshot): self.registers.append(snapshot)
        client = Client()
        result = run_iteration(client, Executor(), State(), {"id": "c1", "lease": "l"}, sleep=lambda _: None)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(client.posts, 3)
        self.assertEqual(client.registers, [{"phase": "idle"}])


if __name__ == "__main__": unittest.main()
