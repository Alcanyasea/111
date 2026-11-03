import argparse
import json
import sys
import time
from pathlib import Path
from .agent import AgentClient, CommandExecutor


def main(argv=None):
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("serve"); s.add_argument("--config", required=True); s.add_argument("--url", required=True); s.add_argument("--device", required=True); s.add_argument("--token", required=True)
    args = p.parse_args(argv)
    if args.command == "serve":
        config_path = Path(args.config).resolve()
        project_root = config_path.parent
        sys.path.insert(0, str(project_root / "gui"))
        import config as appconfig
        from core import runner
        from .local_state import LocalState
        cfg = appconfig.load()
        state = LocalState(project_root)
        executor = CommandExecutor(cfg, runner, None, state)
        client = AgentClient(args.url, args.device, args.token)
        delay = 1
        while True:
            try:
                client.register(state.read_snapshot()); delay = 1; break
            except OSError:
                time.sleep(delay); delay = min(delay * 2, 60)
        while True:
            try:
                item = client.poll(); delay = 1
            except OSError:
                time.sleep(delay); delay = min(delay * 2, 60); continue
            command = item.get("command") or (item if item.get("id") else None)
            if command:
                result = executor.execute(command)
                client.post_result(command["id"], command.get("lease", ""), result)
            time.sleep(5)


if __name__ == "__main__": main()
