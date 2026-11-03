# 远程控制平面与本地 Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a low-resource server control plane and a Windows local Agent so a phone can manage the existing MAA runner without moving emulator, account credentials, or slot data off the local computer.

**Architecture:** The server is a small Python standard-library HTTP service backed by SQLite, serving JSON APIs and a responsive static page. The Windows Agent makes outbound HTTPS requests, claims allow-listed commands, reuses `gui.config`, `gui.core.runner`, `gui.core.scheduler`, and the existing scripts, and uploads only redacted status. Configuration writes use a versioned patch plus a local lock and backup.

**Tech Stack:** Python 3.10+, `http.server`, `sqlite3`, `urllib`, JSON, HTML/CSS/vanilla JavaScript, PowerShell 7, unittest.

**Spec:** `docs/superpowers/specs/2026-11-03-remote-agent-control-design.md`

## Global Constraints

- The 1GB server must not run Windows, MuMu, MAA, or screen recognition.
- Passwords, login tokens, `scripts/accounts`, MAA `gui.json`, complete logs, and screenshots never leave the local computer.
- Server commands are structured allow-listed JSON; no shell strings, paths, or arbitrary PowerShell are accepted.
- The Agent must not stop an existing run on disconnect; it must respect `master.lock`, the console single-instance lock, and local config backups.
- Tests and temporary scripts must use temporary directories and fake runners; never read or write the real `config.json`, accounts, logs, emulator, or MAA directories.
- Before any overwrite or delete of configuration, create or verify a recoverable backup; the Agent must create a timestamped backup before applying a patch.
- Run `python -m unittest discover -s tests` and GUI checks with `gui/.venv/Scripts/python.exe`; run syntax/import checks for affected files.
- Default deployment assumes Linux + systemd or an isolated Docker Compose project, HTTPS through an existing reverse proxy, short TTL for offline start commands, and local configuration as the first-pairing source.

## Review Focus

- Duplicate start/collect commands must be idempotent per device; test the same idempotency key and a local `master.lock`.
- A stale queued start must expire while stop/config commands remain claimable; test command TTLs after reconnect.
- A remote patch based on an old revision must be rejected without changing the local file; test the backup and byte-for-byte preservation.
- Server responses must redact passwords, tokens, slot files, full logs, and screenshots; test serialization with representative account data.
- Existing GUI saves and Agent saves must serialize through one lock and preserve the newest config; test two writers against a temporary config.

### Task 1: Shared protocol, redaction, and SQLite storage

**Files:**
- Create: `remote_control/__init__.py`
- Create: `remote_control/protocol.py`
- Create: `remote_control/storage.py`
- Test: `tests/test_remote_protocol.py`
- Test: `tests/test_remote_storage.py`

**Interfaces:**
- `protocol.command_payload(kind: str, args: dict, idempotency_key: str, expires_at: int) -> dict` validates the allow-list (`run`, `collect`, `stop`, `switch_account`, `patch_config`, `refresh_status`) and rejects unknown keys, shell-like strings, and invalid TTLs.
- `protocol.redact_config(cfg: dict) -> dict` returns only the documented non-secret fields.
- `protocol.redact_status(status: dict) -> dict` removes credentials, tokens, slot contents, full logs, and screenshots.
- `storage.Store(path: Path)` exposes `create_device`, `get_device`, `revoke_device`, `enqueue_command`, `claim_command`, `finish_command`, `save_snapshot`, `get_snapshot`, and `audit` using SQLite transactions.

- [ ] **Step 1: Write failing protocol tests** covering valid payloads, unknown command rejection, shell/path rejection, config redaction, and status redaction.
- [ ] **Step 2: Run `python -m unittest tests.test_remote_protocol -v` and verify the new tests fail because the module is absent.**
- [ ] **Step 3: Implement `protocol.py` with explicit field allow-lists and no filesystem access.**
- [ ] **Step 4: Write failing SQLite tests** for device revocation, idempotency uniqueness, queued/claimed/running/succeeded transitions, lease expiry, and snapshot persistence using `tempfile.TemporaryDirectory`.
- [ ] **Step 5: Run `python -m unittest tests.test_remote_storage -v` and verify storage tests fail before implementation.**
- [ ] **Step 6: Implement `storage.py` with schema creation, foreign keys, unique `(device_id, idempotency_key)`, lease timestamps, and atomic state transitions.**
- [ ] **Step 7: Run both test modules and verify they pass.**
- [ ] **Step 8: Commit `feat: add remote control protocol and queue storage`.**

### Task 2: Server API, authentication, and mobile shell

**Files:**
- Create: `remote_control/server.py`
- Create: `remote_control/web/index.html`
- Create: `remote_control/web/app.js`
- Create: `remote_control/web/styles.css`
- Create: `remote_control/server_cli.py`
- Test: `tests/test_remote_server.py`

**Interfaces:**
- `ControlHTTPServer(store: Store, auth: Auth, bind: str, port: int)` serves `/api/v1/health`, `/api/v1/session`, `/api/v1/devices`, `/api/v1/devices/{id}/commands`, `/api/v1/devices/{id}/snapshot`, `/api/v1/devices/{id}/pair`, and static `/`.
- `Auth.verify_password(password: str) -> bool`, `Auth.issue_session() -> str`, and `Auth.verify_session(token: str) -> bool` use salted hashes and expiring opaque sessions.
- `POST /api/v1/devices/{id}/commands` accepts the payload from `protocol.command_payload`, returns `202` plus `command_id`, and maps duplicate idempotency keys to the original command.
- `GET /api/v1/devices/{id}/commands/next` requires an Agent token and returns one leased command or `204`; `POST .../result` accepts only the matching lease and redacted result.

- [ ] **Step 1: Write failing API tests** for password/session auth, unauthorized access, health, device registration/revocation, command creation, idempotency, Agent claim/result, and static page delivery using an in-process server and temporary SQLite.
- [ ] **Step 2: Run `python -m unittest tests.test_remote_server -v` and verify failures.**
- [ ] **Step 3: Implement request parsing, JSON error envelopes, auth headers, session expiry, device pairing, and command endpoints using `ThreadingHTTPServer`; never execute commands in the server process.**
- [ ] **Step 4: Add the mobile page** with status cards, account enable/order controls, run/collect/stop buttons, schedule editor, and base-schedule editor that call only the JSON API and display command state.
- [ ] **Step 5: Run the API tests and a syntax check: `python -m py_compile remote_control/*.py`.**
- [ ] **Step 6: Commit `feat: add authenticated mobile control server`.**

### Task 3: Local Agent client, command executor, and pairing

**Files:**
- Create: `remote_control/agent.py`
- Create: `remote_control/local_state.py`
- Create: `remote_control/agent_cli.py`
- Test: `tests/test_remote_agent.py`
- Test: `tests/test_remote_local_state.py`

**Interfaces:**
- `AgentClient(base_url: str, device_id: str, token: str, opener=None)` implements `register()`, `poll()`, and `post_result(command_id, lease, result)` over `urllib`; `opener` is injectable for tests.
- `LocalState(root: Path)` implements `read_snapshot()`, `apply_patch(patch, base_revision)`, `acquire_write_lock()`, and `backup_config()`; it delegates normalization to `gui.config` and never exposes secret fields in snapshots.
- `CommandExecutor(cfg, runner_module, scheduler_module, state)` implements `execute(command: dict) -> dict` for the six protocol commands, checks local locks, and returns redacted status/result data.

- [ ] **Step 1: Write failing Agent HTTP tests** with a fake opener for registration, 204 polling, lease/result posting, token errors, and retry backoff.
- [ ] **Step 2: Write failing local-state tests** for secret-free snapshots, timestamped backup creation, valid patch application, stale revision rejection, malformed patch rejection, and concurrent writers.
- [ ] **Step 3: Run the focused tests and verify failures.**
- [ ] **Step 4: Implement `AgentClient` and `LocalState`; use a per-project lock file and atomic temp-file replace, with no access to the real project during tests.**
- [ ] **Step 5: Write failing executor tests** using fake runner/scheduler objects for run/collect/stop/switch, busy-lock refusal, unknown command rejection, and no automatic stop on disconnect.
- [ ] **Step 6: Implement `CommandExecutor`; call existing `runner.start`, `runner.start_collect`, `runner.stop`, and switch helpers only after local checks.**
- [ ] **Step 7: Add `agent_cli.py` with `register`, `run-once`, and `serve` commands; persist only server URL, device id, and token in a dedicated Agent state file.**
- [ ] **Step 8: Run focused tests and `python -m py_compile remote_control/*.py`.**
- [ ] **Step 9: Commit `feat: add local Windows agent and command executor`.**

### Task 4: Coordinate GUI saves and configuration revisions

**Files:**
- Modify: `gui/config.py`
- Create: `gui/core/remote_revision.py`
- Modify: `gui/pages/accounts.py`
- Modify: `gui/pages/dashboard.py`
- Modify: `gui/pages/base_schedule_dialog.py`
- Test: `tests/test_remote_revision.py`

**Interfaces:**
- `gui.core.remote_revision.read_revision(cfg) -> int` and `next_revision(cfg) -> int` store a monotonic local revision in a non-secret metadata field.
- `gui.core.remote_revision.save_with_lock(cfg) -> int` acquires the same lock name used by `LocalState`, writes an atomic backup, saves config, and returns the new revision.

- [ ] **Step 1: Write failing revision tests** for missing revision migration, monotonic saves, lock contention, backup creation, and preserving unknown config fields.
- [ ] **Step 2: Run `python -m unittest tests.test_remote_revision -v` and verify failures.**
- [ ] **Step 3: Implement revision normalization and the shared lock/backup helper without changing account secrets or paths.**
- [ ] **Step 4: Replace direct `appconfig.save(self.cfg)` calls in account, dashboard, and base-schedule save paths with the helper while preserving existing error dialogs and scheduler behavior.**
- [ ] **Step 5: Update Agent `LocalState` to call the same helper and reject stale `base_revision`.**
- [ ] **Step 6: Run affected GUI tests with `gui/.venv/Scripts/python.exe` and the focused revision tests.**
- [ ] **Step 7: Commit `feat: serialize GUI and Agent config writes`.**

### Task 5: Packaging, deployment, and operator documentation

**Files:**
- Create: `remote_control/requirements.txt` (empty unless an implementation dependency is proven necessary)
- Create: `remote_control/control-server.service.example`
- Create: `remote_control/docker-compose.example.yml`
- Create: `remote_control/.env.example`
- Modify: `README.md`
- Modify: `AI_SETUP.md`
- Create: `docs/remote-control.md`
- Test: `tests/test_remote_packaging.py`

**Interfaces:**
- `python -m remote_control.server_cli --db PATH --bind HOST --port PORT` starts the server without modifying the existing service.
- `python -m remote_control.agent_cli serve --config PATH` starts the local Agent and reconnects with bounded backoff.

- [ ] **Step 1: Write packaging tests** that validate example service/Compose files use a distinct service name, directory, port, SQLite path, and no privileged host mounts.
- [ ] **Step 2: Implement example systemd/Compose files with explicit resource limits, log rotation, and environment-based secrets; do not claim a specific server OS is present.**
- [ ] **Step 3: Write the operator guide** covering first pairing from local to server, HTTPS/reverse-proxy setup, account/password boundary, command expiry, revoke/recover, backup restore, and coexistence checks.
- [ ] **Step 4: Add README/AI_SETUP links and a mobile-control troubleshooting section.**
- [ ] **Step 5: Run packaging tests and Markdown/file syntax checks.**
- [ ] **Step 6: Commit `docs: document remote control deployment and recovery`.**

### Task 6: End-to-end verification and handoff

**Files:**
- Modify: `tests/test_remote_server.py`
- Modify: `tests/test_remote_agent.py`
- Create: `tests/test_remote_integration.py`

- [ ] **Step 1: Add an integration test** that starts the server on an ephemeral localhost port, pairs a fake Agent, queues a run command, claims it once, applies a safe config patch, posts a redacted result, and verifies the mobile-facing snapshot.
- [ ] **Step 2: Add disconnect/reconnect coverage** proving stale starts expire and that an existing local run remains untouched while the Agent is offline.
- [ ] **Step 3: Run focused remote tests with `python -m unittest discover -s tests -p 'test_remote*.py'`.**
- [ ] **Step 4: Run syntax/import checks for all changed Python files.**
- [ ] **Step 5: Run the required full suite `python -m unittest discover -s tests`; run GUI tests with `gui/.venv/Scripts/python.exe -m unittest ...` as required by the environment.**
- [ ] **Step 6: Review `git diff --check`, confirm no real user files were touched, and record any environment-only failures before handoff.**
- [ ] **Step 7: Commit `test: verify remote control end to end`.**

