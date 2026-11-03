import json
import copy
import sys
import time
import traceback
from pathlib import Path

from .telegram_base_schedule import (
    DraftStore,
    InviteStore,
    PollState,
    RolePolicy,
    SelectionStore,
    TelegramAPI,
    apply_preset_schedule,
    build_schedule_patch,
    describe_schedule_changes,
    find_preset,
    find_preset_by_hash,
    format_account_detail,
    format_preset_detail,
    make_preset_schedule,
    preset_hash,
    preset_names,
    preset_summary,
    resolve_account_token,
    split_preset_args,
)


def _import_appconfig(project_root):
    """加载项目自带的 gui/config 模块（原子写 + 每日备份都由它负责）。"""
    gui_dir = str(Path(project_root) / "gui")
    if gui_dir not in sys.path:
        sys.path.insert(0, gui_dir)
    import config as appconfig
    return appconfig


def restore_bindings(policy, invites):
    """把历史绑定码的兑换记录并入编辑者绑定；CLI 显式指定的优先。"""
    for user_id, account_id in invites.bindings().items():
        policy.editor_accounts.setdefault(user_id, account_id)
    return policy


def _menu(commands):
    return [{"command": c, "description": d} for c, d in commands]


# 菜单按用户状态切换：未选账号前只显示选账号相关按钮
MENU_DEFAULT = _menu([("start", "开始"), ("help", "帮助")])
MENU_OWNER_PENDING = _menu([("start", "开始"), ("select", "选账号"), ("help", "帮助")])
MENU_OWNER_READY = _menu([
    ("start", "开始"),
    ("config", "查看配置"),
    ("upload", "上传排班"),
    ("presets", "预设管理"),
    ("publish", "发布草稿"),
    ("invite", "邀请编辑者"),
    ("select", "换账号"),
    ("help", "帮助"),
])
MENU_EDITOR_PENDING = _menu([("start", "开始"), ("bind", "绑定账号"), ("help", "帮助")])
MENU_EDITOR_READY = _menu([
    ("start", "开始"),
    ("config", "查看配置"),
    ("upload", "上传排班"),
    ("presets", "预设管理"),
    ("help", "帮助"),
])


class BaseScheduleBot:
    def __init__(self, api, cfg, project_root, drafts, policy, invites=None, invite_account=None,
                 appconfig=None, state=None, selection=None):
        self.api, self.cfg, self.project_root, self.drafts, self.policy, self.invites, self.invite_account = \
            api, cfg, Path(project_root), drafts, policy, invites, invite_account
        self.appconfig = appconfig  # None 时按需从 project_root 导入 gui/config
        self.state = state  # PollState，None 则不持久化 offset
        self.selection = selection  # SelectionStore，None 表示不记选号（全部按未选处理）
        self._plugin_module = None

    def _plugin(self):
        if self._plugin_module is None:
            from .telegram_base_schedule import _load_base_plugin
            self._plugin_module = _load_base_plugin(self.project_root)
        return self._plugin_module

    def _fresh_cfg(self):
        """每条消息都从磁盘重读配置，避免覆盖 GUI 在机器人启动后的修改。"""
        if self.appconfig is None:
            self.appconfig = _import_appconfig(self.project_root)
        cfg = self.appconfig.load()
        if isinstance(cfg, dict):
            self.cfg = cfg
        return cfg

    def _account_label(self, cfg, account_id):
        return next((a.get("label") or account_id for a in cfg.get("accounts") or []
                     if str(a.get("id")) == str(account_id)), account_id)

    def _selected_id(self, user_id):
        return self.selection.get(user_id) if self.selection is not None else None

    def _peek_selected(self, user_id):
        """当前选中账号（按 self.cfg 粗查，仅用于文案/菜单）。"""
        sel = self._selected_id(user_id)
        if not sel:
            return None
        return next((a for a in (self.cfg.get("accounts") or [])
                     if str(a.get("id")) == str(sel)), None)

    def _target_account(self, user_id, cfg):
        """本次操作的目标账号：编辑者=绑定账号；所有者=已选账号，未选则提示。"""
        if not self.policy.is_owner(user_id):
            bound = self.policy.bound_account(user_id)
            if not bound:
                raise PermissionError("账号权限不足")
            acc = next((a for a in cfg.get("accounts") or [] if str(a.get("id")) == str(bound)), None)
            if acc is None:
                raise ValueError("绑定的账号不存在")
            return acc
        sel = self._selected_id(user_id)
        if sel:
            acc = next((a for a in cfg.get("accounts") or [] if str(a.get("id")) == str(sel)), None)
            if acc is not None:
                return acc
            if self.selection is not None:
                self.selection.set(user_id, None)  # 选中的账号已不存在，清除
        raise ValueError("请先 /select 选择账号")

    def _require_target(self, user_id, chat_id, cfg):
        """门禁：未选账号时提示并刷新该聊天的菜单，返回 None。"""
        try:
            return self._target_account(user_id, cfg)
        except PermissionError as exc:
            self.api.send(chat_id, str(exc))
            return None
        except ValueError as exc:
            self._apply_menu(user_id)
            self.api.send(chat_id, str(exc))
            return None

    def _apply_menu(self, user_id):
        """按用户当前状态刷新其聊天菜单（隐藏/显示按钮）。"""
        if self.policy.is_owner(user_id):
            commands = MENU_OWNER_READY if self._peek_selected(user_id) is not None else MENU_OWNER_PENDING
        elif user_id in self.policy.editor_accounts:
            commands = MENU_EDITOR_READY if self.policy.bound_account(user_id) else MENU_EDITOR_PENDING
        else:
            commands = MENU_DEFAULT
        try:
            self.api.set_my_commands(commands, {"type": "chat", "chat_id": user_id})
        except Exception:
            traceback.print_exc()

    def _mutate_account(self, cfg, account_id, mutate):
        """在磁盘最新配置的深拷贝上修改账号字段并原子保存；mutate 抛错则不落盘。"""
        candidate = copy.deepcopy(cfg)
        account = next(a for a in candidate.get("accounts") or [] if str(a.get("id")) == str(account_id))
        mutate(account)
        if self.appconfig is None:
            self.appconfig = _import_appconfig(self.project_root)
        self.appconfig.save(candidate)
        self.cfg = copy.deepcopy(candidate)
        return candidate

    def _save_account_patch(self, cfg, account_id, patch):
        """Save a candidate config atomically, then update the in-memory config."""
        def mutate(account):
            account["base_schedule"] = patch["base_schedule"]
        self._mutate_account(cfg, account_id, mutate)

    def configure_commands(self):
        """默认菜单 + 按当前状态给所有者和已绑定编辑者的聊天菜单。"""
        self.api.set_my_commands(MENU_DEFAULT)
        self._apply_menu(self.policy.owner_id)
        for uid in list(self.policy.editor_accounts):
            self._apply_menu(uid)

    def _send_pretty(self, chat_id, text):
        for start in range(0, len(text), 3500):
            self.api.send(chat_id, text[start:start + 3500])

    def _help_text(self, user_id):
        """按角色给出命令速查（一行一条：命令 + 简短标注）。"""
        if self.policy.is_owner(user_id):
            sel = self._peek_selected(user_id)
            head = ("所有者：当前「%s」，所有操作只针对该账号" % (sel.get("label") or sel.get("id"))
                    if sel is not None else "所有者：先 /select 选账号，再使用其他命令")
        else:
            bound = self.policy.bound_account(user_id)
            head = ("编辑者：绑定「%s」，一切只针对该账号" % self._account_label(self.cfg, bound)
                    if bound else "未绑定：让所有者 /invite，再 /bind 绑定码")
        lines = [
            head,
            "/select 选账号（先选账号，才能用其他命令）",
            "/config 查看当前账号配置",
            "/upload 上传排班（写入当前账号）",
            "/presets 当前账号的预设管理",
            "/preset show 名称 查看预设",
            "/preset use 名称 应用预设",
            "/preset save 名称 [覆盖] 存为预设",
            "/preset del 名称 删除预设",
            "名称含空格用「」包住，如 /preset use 「333 日常」",
        ]
        if self.policy.is_owner(user_id):
            lines += ["/invite 邀请编辑者管理当前账号", "/publish 草稿ID 发布草稿"]
        else:
            lines += ["/bind 绑定码 绑定账号"]
        return "\n".join(lines)

    def handle_message(self, message):
        chat = message.get("chat") or {}
        if str(chat.get("type") or "") != "private":
            return  # 群聊一律忽略，避免误触发
        user_id = str(((message.get("from") or {}).get("id")))
        chat_id = chat.get("id", user_id)
        text = str(message.get("text") or "").strip()
        if text == "/start":
            if not (self.policy.is_owner(user_id) or user_id in self.policy.editor_accounts):
                self.api.send(chat_id, "你没有权限使用此机器人。")
                return
            if self.policy.is_owner(user_id):
                sel = self._peek_selected(user_id)
                info = ("所有者：当前「%s」" % (sel.get("label") or sel.get("id"))
                        if sel is not None else "所有者：请先 /select 选账号")
            else:
                bound = self.policy.bound_account(user_id)
                info = ("编辑者：已绑定「%s」" % self._account_label(self.cfg, bound)
                        if bound else "编辑者：未绑定，先让所有者 /invite，再 /bind 绑定码")
            self.api.send(chat_id, "基建机器人已就绪。\n%s\n发送 /help 看命令" % info)
            return
        if text == "/help":
            self._send_pretty(chat_id, self._help_text(user_id))
            return
        if text == "/select" or text.startswith("/select "):
            try:
                self._handle_select(user_id, chat_id, text)
            except Exception as exc:
                self.api.send(chat_id, "选择失败：%s" % str(exc))
            return
        if text == "/upload":
            self.api.send(chat_id, "请发送 .json 排班文件；会写入当前选定的账号（/select 选账号）。")
            return
        if text == "/config" or text.startswith("/config "):
            try:
                cfg = self._fresh_cfg()
                account = self._require_target(user_id, chat_id, cfg)
                if account is not None:
                    self._send_pretty(chat_id, format_account_detail(self._plugin(), account))
            except Exception as exc:
                self.api.send(chat_id, "查看失败：%s" % str(exc))
            return
        if text == "/presets" or text.startswith("/presets ") or text == "/preset" or text.startswith("/preset "):
            try:
                self._handle_presets(user_id, chat_id, text)
            except Exception as exc:
                self.api.send(chat_id, "预设操作失败：%s" % str(exc))
            return
        if text == "/invite":
            if not self.policy.is_owner(user_id):
                self.api.send(chat_id, "只有所有者可以生成绑定码。")
                return
            cfg = self._fresh_cfg()
            account = self._require_target(user_id, chat_id, cfg)
            if account is None:
                return
            code = self.invites.create(str(account.get("id")))
            self.api.send(chat_id, "绑定码（10分钟内有效、只能使用一次），绑定账号「%s」：\n%s"
                          % (account.get("label") or account.get("id"), code))
            return
        if text.startswith("/bind "):
            try:
                account_id = self.invites.redeem(text.split(None, 1)[1], user_id)
                self.policy.editor_accounts[user_id] = account_id
                self._apply_menu(user_id)
                self.api.send(chat_id, "绑定成功，只能管理账号「%s」。" % self._account_label(self._fresh_cfg(), account_id))
            except Exception as exc:
                self.api.send(chat_id, "绑定失败：%s" % exc)
            return
        if text.startswith("/publish "):
            if not self.policy.is_owner(user_id):
                self.api.send(chat_id, "只有所有者可以发布草稿。")
                return
            draft_id = text.split(None, 1)[1].strip()
            try:
                cfg = self._fresh_cfg()
                sel = self._selected_id(user_id)
                if not sel:
                    self._apply_menu(user_id)
                    self.api.send(chat_id, "请先 /select 选择账号，再发布草稿。")
                    return
                draft = self.drafts.peek(draft_id)
                if not draft or draft.get("status") != "draft":
                    self.api.send(chat_id, "发布失败：草稿不存在或已发布")
                    return
                if str(sel) != str(draft.get("account_id")):
                    label = self._account_label(cfg, draft.get("account_id"))
                    idx = next((i for i, a in enumerate(cfg.get("accounts") or [], 1)
                                if str(a.get("id")) == str(draft.get("account_id"))), None)
                    hint = ("/select %d" % idx) if idx else "/select"
                    self.api.send(chat_id, "该草稿属于账号「%s」，请先 %s 再发布。" % (label, hint))
                    return
                self.drafts.take(draft_id, user_id, self.policy.owner_id)
                self._save_account_patch(cfg, draft["account_id"], draft["patch"])
                self.api.send(chat_id, "已发布到账号「%s」。" % self._account_label(self.cfg, draft["account_id"]))
            except Exception as exc:
                self.api.send(chat_id, "发布失败：%s" % str(exc))
            return
        document = message.get("document")
        if document:
            self._handle_document(user_id, chat_id, document)
            return
        if text.startswith("/"):
            self.api.send(chat_id, "未知命令。发送 /help 看命令")

    def _handle_select(self, user_id, chat_id, text):
        if not self.policy.is_owner(user_id):
            self.api.send(chat_id, "编辑者固定绑定账号，无需选择。")
            return
        arg = text[len("/select"):].strip()
        cfg = self._fresh_cfg()
        accounts = cfg.get("accounts") or []
        if not accounts:
            self.api.send(chat_id, "暂无账号。")
            return
        if not arg:
            sel = self._peek_selected(user_id)
            head = ("当前已选：「%s」\n" % (sel.get("label") or sel.get("id"))) if sel is not None else ""
            self.api.send(chat_id, head + "点按钮选择要操作的账号：",
                          reply_markup=self._select_keyboard(accounts))
            return
        account = resolve_account_token(accounts, arg)  # 文本方式兜底
        self._mark_selected(user_id, chat_id, account)

    @staticmethod
    def _select_keyboard(accounts):
        """账号选择按钮：一行一个账号，callback 数据为 sel:<账号id>。"""
        rows = [[{"text": str(a.get("label") or a.get("id") or ""),
                  "callback_data": "sel:%s" % a.get("id")}] for a in accounts]
        return {"inline_keyboard": rows}

    def _mark_selected(self, user_id, chat_id, account, source_message=None):
        """记录选中 + 刷新菜单 + 回执；来源是按钮消息时原地编辑。"""
        if self.selection is not None:
            self.selection.set(user_id, str(account.get("id")))
        self._apply_menu(user_id)
        label = str(account.get("label") or account.get("id") or "")
        text = "已选「%s」，菜单已更新。\n/config 查看配置 · /upload 上传排班 · /presets 管理预设" % label
        if source_message is not None:
            try:
                self.api.edit_message_text(source_message.get("chat", {}).get("id"),
                                           source_message.get("message_id"), text)
                return
            except Exception:
                traceback.print_exc()
        self.api.send(chat_id, text)

    def _answer(self, cb, text):
        try:
            self.api.answer_callback_query(cb.get("id"), text)
        except Exception:
            traceback.print_exc()

    def _handle_callback_safe(self, cb):
        try:
            self._handle_callback(cb)
        except Exception:
            traceback.print_exc()
            self._answer(cb, "处理失败")

    def _handle_callback(self, cb):
        data = str(cb.get("data") or "")
        user_id = str((cb.get("from") or {}).get("id"))
        message = cb.get("message") or {}
        chat = message.get("chat") or {}
        if str(chat.get("type") or "") != "private":
            self._answer(cb, None)
            return
        if data.startswith("sel:"):
            if not self.policy.is_owner(user_id):
                self._answer(cb, "只有所有者需要选账号")
                return
            account_id = data[len("sel:"):]
            cfg = self._fresh_cfg()
            account = next((a for a in cfg.get("accounts") or [] if str(a.get("id")) == account_id), None)
            if account is None:
                self._answer(cb, "该账号已不存在")
                self.api.send(chat.get("id"), "该账号已不存在，请重新 /select。")
                return
            self._answer(cb, "已选「%s」" % (account.get("label") or account.get("id")))
            self._mark_selected(user_id, chat.get("id"), account, source_message=message)
            return
        if data.split(":", 1)[0] in ("preshow", "preuse", "predel", "predelok", "preback"):
            self._handle_preset_callback(cb, user_id, chat, message, data)
            return
        self._answer(cb, None)

    def _preset_cb_ctx(self, cb, user_id, chat, message, data):
        """解析预设回调 → (cfg, account, preset, digest)；失效时应答并原地提示。

        preback 只带账号（无 digest），返回的 preset/digest 为 None。
        """
        parts = data.split(":")
        if len(parts) < 2:
            self._answer(cb, None)
            return None
        acc_id = parts[1]
        digest = parts[2] if len(parts) > 2 else None
        cfg = self._fresh_cfg()
        try:
            account = self._target_account(user_id, cfg)
        except (PermissionError, ValueError) as exc:
            self._answer(cb, str(exc))
            return None
        if str(account.get("id")) != acc_id:
            self._answer(cb, "账号已切换")
            self._edit_or_send(chat.get("id"), message, "账号已切换，请重新发送 /presets。")
            return None
        if digest is None:
            return cfg, account, None, None
        preset = find_preset_by_hash(account, digest)
        if preset is None:
            self._answer(cb, "预设列表已变化")
            self._edit_or_send(chat.get("id"), message, "预设列表已变化，请重新发送 /presets。")
            return None
        return cfg, account, preset, digest

    def _handle_preset_callback(self, cb, user_id, chat, message, data):
        ctx = self._preset_cb_ctx(cb, user_id, chat, message, data)
        if ctx is None:
            return
        cfg, account, preset, digest = ctx
        action = data.split(":", 1)[0]
        acc_id = str(account.get("id"))
        if action == "preshow":
            self._answer(cb, None)
            self._edit_or_send(
                chat.get("id"), message,
                "「%s」\n%s" % (preset.get("name"), format_preset_detail(self._plugin(), preset.get("schedule"))),
                {"inline_keyboard": [[
                    {"text": "▶ 应用", "callback_data": "preuse:%s:%s" % (acc_id, digest)},
                    {"text": "🗑 删除", "callback_data": "predel:%s:%s" % (acc_id, digest)},
                    {"text": "◀ 返回列表", "callback_data": "preback:%s" % acc_id},
                ]]})
        elif action == "preuse":
            self._answer(cb, "应用中…")
            text = self._apply_preset_flow(cfg, account, preset)
            self._edit_or_send(chat.get("id"), message, text,
                               {"inline_keyboard": [[{"text": "◀ 返回列表", "callback_data": "preback:%s" % acc_id}]]})
        elif action == "predel":
            self._answer(cb, None)
            self._edit_or_send(
                chat.get("id"), message,
                "确认删除预设「%s」？（已应用到排班的内容不受影响）" % preset.get("name"),
                {"inline_keyboard": [[
                    {"text": "🗑 确认删除", "callback_data": "predelok:%s:%s" % (acc_id, digest)},
                    {"text": "◀ 取消", "callback_data": "preback:%s" % acc_id},
                ]]})
        elif action == "predelok":
            self._answer(cb, "已删除")
            text = self._delete_preset_flow(cfg, account, preset)
            self._edit_or_send(chat.get("id"), message, text,
                               {"inline_keyboard": [[{"text": "◀ 返回列表", "callback_data": "preback:%s" % acc_id}]]})
        elif action == "preback":
            self._answer(cb, None)
            self._edit_or_send(chat.get("id"), message, self._presets_manage_text(account),
                               self._presets_manage_markup(account))

    def _edit_or_send(self, chat_id, message, text, markup=None):
        """原地编辑按钮消息；编辑失败（如消息太旧）退化为发新消息。"""
        try:
            self.api.edit_message_text(chat_id, message.get("message_id"), text, reply_markup=markup)
        except Exception:
            traceback.print_exc()
            self.api.send(chat_id, text, reply_markup=markup)

    def _handle_presets(self, user_id, chat_id, text):
        cfg = self._fresh_cfg()
        account = self._require_target(user_id, chat_id, cfg)
        if account is None:
            return
        parts = text.split()
        if parts[0] == "/presets":
            self.api.send(chat_id, self._presets_manage_text(account),
                          reply_markup=self._presets_manage_markup(account))
            return
        if len(parts) < 2:
            self.api.send(chat_id, self._preset_usage())
            return
        action = parts[1].lower()
        rest = text.split(None, 2)[2] if len(text.split(None, 2)) > 2 else ""
        name, _extra = split_preset_args(rest)
        if not name:
            self.api.send(chat_id, self._preset_usage())
            return
        plugin = self._plugin()
        account_id = str(account.get("id"))
        if action == "show":
            preset = find_preset(account, name)
            if preset is None:
                raise ValueError(self._preset_not_found(account, name))
            self._send_pretty(chat_id, "\n".join([
                "「%s」" % name,
                format_preset_detail(plugin, preset.get("schedule")),
                "",
                "应用：" + self._preset_cmd("use", name),
                "删除：" + self._preset_cmd("del", name),
            ]))
        elif action == "use":
            preset = find_preset(account, name)
            if preset is None:
                raise ValueError(self._preset_not_found(account, name))
            self.api.send(chat_id, self._apply_preset_flow(cfg, account, preset))
        elif action == "save":
            overwrite = "覆盖" in _extra
            existing = find_preset(account, name)
            if existing is not None and not overwrite:
                raise ValueError("已有同名预设「%s」。\n确认覆盖：%s 覆盖" % (name, self._preset_cmd("save", name)))
            schedule = make_preset_schedule(account.get("base_schedule"))
            if schedule is None:
                raise ValueError("账号「%s」还没有排班内容，先上传排班或应用预设后再保存。"
                                 % self._account_label(cfg, account_id))

            def mutate(acc):
                # 与控制台一致：同名预设原地覆盖（保序），否则追加
                items = [p for p in (acc.get("base_schedule_presets")
                                     if isinstance(acc.get("base_schedule_presets"), list) else [])
                         if isinstance(p, dict)]
                for p in items:
                    if str(p.get("name") or "").strip() == name:
                        p["schedule"] = schedule
                        break
                else:
                    items.append({"name": name, "schedule": schedule})
                acc["base_schedule_presets"] = items
            self._mutate_account(cfg, account_id, mutate)
            self.api.send(chat_id, "已%s预设「%s」（账号「%s」，布局%s，%d个批次）。\n应用：%s" % (
                "覆盖保存" if existing is not None else "保存",
                name, self._account_label(self.cfg, account_id),
                schedule.get("layout"), len(schedule["batches"]),
                self._preset_cmd("use", name)))
        elif action == "del":
            preset = find_preset(account, name)
            if preset is None:
                raise ValueError(self._preset_not_found(account, name))
            self.api.send(chat_id, self._delete_preset_flow(cfg, account, preset))
        else:
            self.api.send(chat_id, self._preset_usage())

    def _apply_preset_flow(self, cfg, account, preset):
        """应用预设并返回回执文本（覆盖/保留批次 + 变更对比 + 启用状态）。"""
        plugin = self._plugin()
        account_id = str(account.get("id"))
        name = str(preset.get("name") or "").strip()
        names = plugin.schedule_spec(cfg)[1]
        new_bs, matched, kept = apply_preset_schedule(plugin, account, preset, names)
        changes = describe_schedule_changes(plugin, account.get("base_schedule") or {}, new_bs)

        def mutate(acc):
            acc["base_schedule"] = new_bs
        self._mutate_account(cfg, account_id, mutate)
        lines = ["已把预设「%s」应用到账号「%s」。" % (name, self._account_label(self.cfg, account_id))]
        if matched:
            lines.append("覆盖批次：" + "、".join(matched))
        if kept:
            lines.append("保留当前内容（预设里没有该批次）：" + "、".join(kept))
        if changes:
            lines.append("变更：")
            lines += ["· " + c for c in changes[:10]]
            if len(changes) > 10:
                lines.append("… 共 %d 处变更" % len(changes))
        lines.append("基建当前：%s（不改启用）"
                     % ("✅启用" if new_bs.get("enabled") else "⛔停用"))
        lines.append("存为预设：/preset save 名称 [覆盖]")
        return "\n".join(lines)

    def _delete_preset_flow(self, cfg, account, preset):
        """删除预设并返回回执文本。"""
        name = str(preset.get("name") or "").strip()
        account_id = str(account.get("id"))

        def mutate(acc):
            items = acc.get("base_schedule_presets")
            acc["base_schedule_presets"] = [p for p in (items if isinstance(items, list) else [])
                                            if not (isinstance(p, dict) and str(p.get("name") or "").strip() == name)]
        self._mutate_account(cfg, account_id, mutate)
        return ("已删除预设「%s」（账号「%s」）。已应用到排班的内容不受影响。"
                % (name, self._account_label(self.cfg, account_id)))

    def _presets_manage_text(self, account):
        """当前账号的预设管理页文本（操作按钮见 _presets_manage_markup）。"""
        label = str(account.get("label") or account.get("id") or "")
        items = account.get("base_schedule_presets")
        items = items if isinstance(items, list) else []
        lines = ["「%s」的预设：" % label]
        if not items:
            lines.append("（还没有预设）")
        for it in items:
            if isinstance(it, dict):
                lines.append("· " + preset_summary(it))
        lines.append("")
        lines.append("点下方按钮查看/应用/删除；保存当前排班：/preset save 名称 [覆盖]")
        return "\n".join(lines)

    def _presets_manage_markup(self, account):
        """预设管理页按钮：每条预设一行 [📄 名称] [▶ 应用] [🗑 删除]。"""
        acc_id = str(account.get("id"))
        rows = []
        for it in account.get("base_schedule_presets") or []:
            if not isinstance(it, dict):
                continue
            digest = preset_hash(it.get("name") or "")
            rows.append([
                {"text": "📄 %s" % preset_summary(it), "callback_data": "preshow:%s:%s" % (acc_id, digest)},
                {"text": "▶ 应用", "callback_data": "preuse:%s:%s" % (acc_id, digest)},
                {"text": "🗑 删除", "callback_data": "predel:%s:%s" % (acc_id, digest)},
            ])
        return {"inline_keyboard": rows} if rows else None

    @staticmethod
    def _preset_cmd(action, name):
        """生成可直接复制发送的预设命令；名称含空格时用「」包住。"""
        nm = name if (name and " " not in name) else "「%s」" % name
        return "/preset %s %s" % (action, nm)

    @staticmethod
    def _preset_not_found(account, name):
        names = preset_names(account)
        if names:
            return "找不到预设「%s」。该账号可用预设：%s" % (name, "、".join(names))
        return "找不到预设「%s」。该账号还没有任何预设。" % name

    @staticmethod
    def _preset_usage():
        return ("预设：/preset show/use/save/del 名称\n"
                "show 查看 · use 应用 · save 保存（同名加 覆盖） · del 删除\n"
                "名称含空格用「」包住；/presets 看列表")

    def _upload_reply(self, summary, changes, notes):
        lines = [summary]
        if changes:
            lines.append("变更：")
            lines += ["· " + c for c in changes[:10]]
            if len(changes) > 10:
                lines.append("… 共 %d 处变更" % len(changes))
        if notes:
            lines.append("注意：")
            lines += ["· " + n for n in notes[:5]]
            if len(notes) > 5:
                lines.append("… 共 %d 条注意" % len(notes))
        return "\n".join(lines)

    def _handle_document(self, user_id, chat_id, document):
        try:
            filename = str(document.get("file_name") or "").lower()
            if not filename.endswith(".json"):
                raise ValueError("只接受 .json 基建排班文件")
            cfg = self._fresh_cfg()
            account = self._require_target(user_id, chat_id, cfg)
            if account is None:
                return
            account_id = str(account.get("id"))
            patch, summary, notes = build_schedule_patch(
                cfg, account_id, self.api.download_document(document["file_id"]), self.project_root)
            changes = describe_schedule_changes(
                self._plugin(), account.get("base_schedule") or {}, patch.get("base_schedule") or {})
            reply = self._upload_reply(summary, changes, notes)
            if self.policy.is_owner(user_id):
                draft_id = self.drafts.create(user_id, account_id, patch, summary)
                reply += "\n\n草稿 ID：%s\n发布请回复 /publish %s" % (draft_id, draft_id)
            else:
                self._save_account_patch(cfg, account_id, patch)
                reply += "\n已直接写入本地配置。"
            self.api.send(chat_id, reply)
        except Exception as exc:
            self.api.send(chat_id, "上传失败：%s" % str(exc))

    def _handle_update_safe(self, item):
        message = item.get("message") or {}
        try:
            self.handle_message(message)
        except Exception:
            traceback.print_exc()
            try:
                chat_id = (message.get("chat") or {}).get("id")
                if chat_id is not None:
                    self.api.send(chat_id, "处理失败，请稍后重试。")
            except Exception:
                traceback.print_exc()

    def _poll_once(self, offset):
        """拉取并处理一轮更新；返回 (新 offset, 是否正常)。错误不外抛。"""
        try:
            updates = self.api.updates(offset)
        except Exception:
            traceback.print_exc()
            return offset, False
        for item in updates:
            offset = item["update_id"] + 1
            callback = item.get("callback_query")
            if callback is not None:
                self._handle_callback_safe(callback)
            else:
                self._handle_update_safe(item)
        if updates and self.state is not None:
            try:
                self.state.save(offset)
            except OSError:
                traceback.print_exc()
        return offset, True

    def run(self):
        offset = self.state.load() if self.state is not None else None
        while True:
            offset, ok = self._poll_once(offset)
            if not ok:
                time.sleep(5)  # 拉取失败退避；正常时空轮询由 Telegram 长连接阻塞


def main():
    import argparse
    p = argparse.ArgumentParser(description="Telegram 精确基建配置机器人")
    p.add_argument("--token", default=None); p.add_argument("--token-env", default="MAA_TELEGRAM_TOKEN"); p.add_argument("--owner-id", required=True); p.add_argument("--editor-id", action="append", default=[]); p.add_argument("--editor-account", default=None); p.add_argument("--editor-account-label", default=None); p.add_argument("--project", required=True); p.add_argument("--drafts", required=True); p.add_argument("--invites", required=True); p.add_argument("--state", default=None); p.add_argument("--selection", default=None)
    args = p.parse_args()
    import os
    token = args.token or os.environ.get(args.token_env)
    if not token:
        p.error("需要 --token 或环境变量 " + args.token_env)
    appconfig = _import_appconfig(args.project)
    cfg = appconfig.load()
    editor_account = args.editor_account
    if not editor_account and args.editor_account_label:
        matches = [a for a in cfg.get("accounts") or [] if str(a.get("label") or "") == args.editor_account_label]
        if len(matches) != 1:
            p.error("账号名称未唯一匹配：%s" % args.editor_account_label)
        editor_account = str(matches[0].get("id"))
    if not editor_account:
        p.error("需要 --editor-account 或 --editor-account-label")
    policy = RolePolicy(args.owner_id, {x: editor_account for x in args.editor_id})
    invites = InviteStore(args.invites)
    restore_bindings(policy, invites)
    state_path = args.state or str(Path(args.drafts).parent / "poll_state.json")
    selection_path = args.selection or str(Path(args.drafts).parent / "selection.json")
    bot = BaseScheduleBot(TelegramAPI(token), cfg, args.project, DraftStore(args.drafts), policy,
                          invites, editor_account, appconfig=appconfig, state=PollState(state_path),
                          selection=SelectionStore(selection_path))
    bot.configure_commands()
    bot.run()


if __name__ == "__main__": main()
