import copy
import re

ALLOWED_COMMANDS = {"run", "collect", "stop", "switch_account", "patch_config", "refresh_status"}
SECRET_KEYS = {"password", "token", "access_token", "uid", "username"}
SHELL_RE = re.compile(r"(?:[;&|`$]|\b(?:pwsh|powershell|cmd|bash|sh)\b|[A-Za-z]:\\)", re.I)


class ProtocolError(ValueError):
    pass


def _safe_value(value):
    if isinstance(value, str) and SHELL_RE.search(value):
        raise ProtocolError("shell-like value rejected")
    if isinstance(value, dict):
        return {str(k): _safe_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_value(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise ProtocolError("unsupported value")


def command_payload(kind, args, idempotency_key, expires_at):
    if kind not in ALLOWED_COMMANDS:
        raise ProtocolError("unknown command")
    if not isinstance(args, dict) or not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ProtocolError("invalid command fields")
    if not isinstance(expires_at, int) or expires_at <= 0:
        raise ProtocolError("invalid expiry")
    return {"kind": kind, "args": _safe_value(copy.deepcopy(args)), "idempotency_key": idempotency_key.strip(), "expires_at": expires_at}


def redact_config(cfg):
    out = {"revision": int(cfg.get("remote_revision", 0) or 0), "accounts": [], "schedule": {}}
    for acc in cfg.get("accounts") or []:
        if not isinstance(acc, dict):
            continue
        slot = str(acc.get("slot") or "")
        out["accounts"].append({"id": str(acc.get("id") or ""), "label": str(acc.get("label") or ""), "server": str(acc.get("server") or "official"), "enabled": bool(acc.get("enabled", True)), "slot_present": bool(slot) and False})
    sched = cfg.get("schedule")
    if isinstance(sched, dict):
        out["schedule"] = {"times": copy.deepcopy(sched.get("times") or []), "collect_times": copy.deepcopy(sched.get("collect_times") or [])}
    return out


def redact_status(status):
    if not isinstance(status, dict):
        return {}
    def clean(value, key=""):
        if key.lower() in SECRET_KEYS or key.lower() in {"log", "logs", "screenshot", "screenshots", "slot_data"}:
            return None
        if isinstance(value, dict):
            return {k: v for k, raw in value.items() if (v := clean(raw, str(k))) is not None}
        if isinstance(value, list):
            return [clean(v, key) for v in value]
        return value
    return clean(status)
