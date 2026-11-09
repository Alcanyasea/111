import json
import sys
import time
from urllib.request import Request, build_opener


class AgentClient:
    def __init__(self, base_url, device_id, token, opener=None):
        self.base_url = base_url.rstrip("/"); self.device_id = device_id; self.token = token; self.opener = opener or build_opener()

    def _request(self, method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(self.base_url + path, data=data, method=method, headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        response = self.opener.open(req, timeout=30)
        try:
            return json.loads(response.read() or b"{}")
        finally:
            close = getattr(response, "close", None)
            if close: close()

    def register(self, snapshot=None): return self._request("POST", "/api/v1/devices/%s/register" % self.device_id, snapshot or {})
    def poll(self): return self._request("GET", "/api/v1/devices/%s/commands/next" % self.device_id)
    def post_result(self, command_id, lease, result): return self._request("POST", "/api/v1/devices/%s/commands/result" % self.device_id, {"command_id": command_id, "lease": lease, **result})


class CommandExecutor:
    def __init__(self, cfg, runner_module, scheduler_module, state, config_loader=None):
        self.cfg, self.runner, self.scheduler, self.state = cfg, runner_module, scheduler_module, state
        self.config_loader = config_loader

    def execute(self, command):
        payload = command.get("payload", command); kind = payload.get("kind")
        if kind in {"run", "collect", "switch_account"} and self.runner.is_running(): return {"state": "failed", "error": "busy"}
        try:
            if kind == "run": self.runner.start(self.cfg)
            elif kind == "collect": self.runner.start_collect(self.cfg)
            elif kind == "stop": self.runner.stop()
            elif kind == "switch_account": self.runner.start_switch(self.cfg, payload.get("args", {}).get("slot"))
            elif kind == "patch_config":
                self.state.apply_patch(payload.get("args", {}).get("patch", {}), payload.get("args", {}).get("base_revision", -1))
                # The running Agent must not keep launching with the pre-patch
                # config after a remote change.
                if self.config_loader:
                    loaded = self.config_loader()
                    if isinstance(loaded, dict):
                        self.cfg = loaded
            elif kind == "refresh_status": pass
            else: return {"state": "failed", "error": "unknown command"}
            return {"state": "succeeded", "result": {"kind": kind}}
        except Exception: return {"state": "failed", "error": "execution_failed"}


def _report_result(client, command, result, sleep=time.sleep):
    """Post a result without letting a transient network failure kill the Agent."""
    delay = 1
    for attempt in range(3):
        try:
            client.post_result(command["id"], command.get("lease", ""), result)
            return True
        except OSError:
            if attempt == 2:
                print("result upload failed; command will be retried by lease", file=sys.stderr)
                return False
            sleep(delay); delay = min(delay * 2, 8)
    return False


def _refresh_snapshot(client, state):
    """Registration is also the status refresh path; a failure must not stop polling."""
    try:
        client.register(state.read_snapshot())
        return True
    except Exception:
        print("snapshot refresh failed", file=sys.stderr)
        return False


def run_iteration(client, executor, state, item, sleep=time.sleep):
    command = item.get("command") or (item if item.get("id") else None)
    if not command:
        return None
    result = executor.execute(command)
    _report_result(client, command, result, sleep=sleep)
    # Covers patch_config and refresh_status, and keeps the server's view fresh
    # after any command that may have changed local state.
    _refresh_snapshot(client, state)
    return result
