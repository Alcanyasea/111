import json
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
    def __init__(self, cfg, runner_module, scheduler_module, state): self.cfg, self.runner, self.scheduler, self.state = cfg, runner_module, scheduler_module, state

    def execute(self, command):
        payload = command.get("payload", command); kind = payload.get("kind")
        if kind in {"run", "collect", "switch_account"} and self.runner.is_running(): return {"state": "failed", "error": "busy"}
        try:
            if kind == "run": self.runner.start(self.cfg)
            elif kind == "collect": self.runner.start_collect(self.cfg)
            elif kind == "stop": self.runner.stop()
            elif kind == "switch_account": self.runner.start_switch(self.cfg, payload.get("args", {}).get("slot"))
            elif kind == "patch_config": self.state.apply_patch(payload.get("args", {}).get("patch", {}), payload.get("args", {}).get("base_revision", -1))
            elif kind == "refresh_status": pass
            else: return {"state": "failed", "error": "unknown command"}
            return {"state": "succeeded", "result": {"kind": kind}}
        except Exception: return {"state": "failed", "error": "execution_failed"}
