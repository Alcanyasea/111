import json
import sys
import tempfile
import unittest
from pathlib import Path

from remote_control.telegram_base_schedule import (
    RolePolicy,
    build_schedule_patch,
    describe_schedule_changes,
    format_account_detail,
    format_account_overview,
    resolve_account_token,
    DraftStore,
    InviteStore,
    PollState,
    SelectionStore,
    TelegramAPI,
)
from remote_control.telegram_bot import BaseScheduleBot, restore_bindings


PROJECT = Path(__file__).parents[1]


class FakeAppConfig:
    """替代 gui/config：内存中的「磁盘」状态，不读写真实 config.json。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.saved = []

    def load(self):
        return self.cfg

    def save(self, value):
        self.saved.append(value)
        self.cfg = value


class Api:
    def __init__(self):
        self.sent = []       # (chat, text, reply_markup)
        self.menus = []      # (commands, scope)
        self.edits = []      # (chat, message_id, text)
        self.answers = []    # (callback_query_id, text)

    def send(self, chat, text, reply_markup=None):
        self.sent.append((chat, text, reply_markup))

    def set_my_commands(self, commands, scope=None):
        self.menus.append((commands, scope))

    def edit_message_text(self, chat, message_id, text, reply_markup=None):
        self.edits.append((chat, message_id, text, reply_markup))

    def answer_callback_query(self, callback_query_id, text=None):
        self.answers.append((callback_query_id, text))

    def download_document(self, file_id):
        rooms = {"manufacture": [{"product": "Pure Gold", "operators": []}] * 4,
                 "trading": [{"product": "LMD", "operators": []}] * 2}
        return json.dumps({"plans": [{"period": [["00:00", "07:59"]], "rooms": rooms}]}).encode()


def make_bot(api, cfg, policy, td, appconfig=None, state=None, invites=None, invite_account=None,
             selection=None):
    return BaseScheduleBot(api, cfg, PROJECT, DraftStore(Path(td) / "d.json"), policy,
                           invites, invite_account,
                           appconfig=appconfig or FakeAppConfig(cfg), state=state,
                           selection=selection or SelectionStore(Path(td) / "sel.json"))


def send_as(bot, uid, text):
    bot.handle_message({"from": {"id": uid}, "chat": {"id": uid, "type": "private"}, "text": text})
    return bot.api.sent[-1][1]


def full_base_schedule(plugin):
    """一个填了部分干员的完整 base_schedule，用于排版断言。"""
    bs = plugin.default_base_schedule()
    bs["enabled"] = True
    batch = bs["batches"]["4点"]
    batch["control"] = ["夜莺", "闪灵", "", "", ""]
    batch["manufacture"][0] = {"product": "Pure Gold", "operators": ["能天使", "", ""]}
    batch["dormitory"][0] = ["暮落", "", "", "", ""]
    batch["fiammetta"] = {"enable": True, "target": "清流"}
    bs["drones"] = {"room": "manufacture", "index": 1, "enable": True, "order": "pre"}
    return bs


class TelegramBaseScheduleTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "schedule": {"times": [{"time": "04:00"}, {"time": "16:00"}]},
            "accounts": [
                {"id": "a1", "label": "官服1", "base_schedule": {"enabled": False}},
                {"id": "a2", "label": "官服2", "base_schedule": {"enabled": False}},
            ],
        }

    def test_owner_can_edit_all_and_editor_only_assigned_account(self):
        policy = RolePolicy(owner_id="1", editor_accounts={"2": "a1"})
        self.assertEqual(policy.account_for("1", "a2"), "a2")
        self.assertEqual(policy.account_for("2", "a1"), "a1")
        with self.assertRaises(PermissionError): policy.account_for("2", "a2")
        with self.assertRaises(PermissionError): policy.account_for("9", "a1")

    def test_upload_rejects_full_config_secrets(self):
        raw = json.dumps({"accounts": [{"password": "secret"}]}).encode()
        with self.assertRaises(ValueError): build_schedule_patch(self.cfg, "a1", raw)

    def test_upload_converts_maa_plan_to_account_patch(self):
        rooms = {"manufacture": [{"product": "Pure Gold", "operators": ["能天使"]}] * 4, "trading": [{"product": "LMD", "operators": []}] * 2}
        raw = json.dumps({"plans": [{"name": "夜班", "period": [["00:00", "07:59"]], "rooms": rooms}]}).encode()
        patch, summary, notes = build_schedule_patch(self.cfg, "a1", raw)
        self.assertTrue(patch["base_schedule"]["enabled"])
        self.assertEqual(patch["base_schedule"]["batches"]["4点"]["manufacture"][0]["operators"][0], "能天使")
        self.assertIn("官服1", summary)
        self.assertEqual(notes, [])

    def test_draft_store_publish_requires_owner(self):
        with tempfile.TemporaryDirectory() as td:
            store = DraftStore(Path(td) / "drafts.json")
            draft_id = store.create("2", "a1", {"enabled": True}, "preview")
            with self.assertRaises(PermissionError): store.take(draft_id, "2", "1")
            draft = store.take(draft_id, "1", "1")
            self.assertEqual(draft["account_id"], "a1")

    def test_editor_uploads_bound_account_directly(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            fake = FakeAppConfig(self.cfg)
            bot = make_bot(api, self.cfg, RolePolicy("1", {"2": "a1"}), td, appconfig=fake)
            msg = {"from": {"id": 2}, "chat": {"id": 2, "type": "private"}, "caption": "a2", "document": {"file_name": "base.json", "file_id": "f"}}
            bot.handle_message(msg)
            # 写入的是候选配置对象；测试自身的 self.cfg 快照不被就地修改
            self.assertTrue(fake.cfg["accounts"][0]["base_schedule"]["enabled"])
            self.assertFalse(fake.cfg["accounts"][1]["base_schedule"]["enabled"])
            self.assertTrue(bot.cfg["accounts"][0]["base_schedule"]["enabled"])
            self.assertEqual(len(fake.saved), 1)  # 只保存一次，且写的就是传给 save 的候选
            self.assertIn("官服1", api.sent[-1][1])
            self.assertNotIn("草稿 ID", api.sent[-1][1])
            self.assertFalse((Path(td) / "d.json").exists())

    def test_owner_invite_code_binds_one_user_once(self):
        with tempfile.TemporaryDirectory() as td:
            store = InviteStore(Path(td) / "invites.json")
            code = store.create("a1", ttl=600)
            self.assertEqual(store.redeem(code, "editor-1"), "a1")
            with self.assertRaises(ValueError): store.redeem(code, "editor-2")

    def test_command_menu_gates_owner_until_account_selected(self):
        api = Api()
        bot = BaseScheduleBot(api, self.cfg, PROJECT, DraftStore(Path(tempfile.mkdtemp()) / "d.json"),
                              RolePolicy("1", {"2": "a1"}))
        bot.configure_commands()
        # 默认菜单 + 所有者聊天菜单（未选账号）+ 编辑者聊天菜单
        self.assertEqual(len(api.menus), 3)
        default, owner_menu, editor_menu = api.menus
        self.assertEqual([c["command"] for c in default[0]], ["start", "help"])
        self.assertIsNone(default[1])
        self.assertEqual([c["command"] for c in owner_menu[0]], ["start", "select", "help"])
        self.assertEqual(owner_menu[1], {"type": "chat", "chat_id": "1"})
        self.assertEqual([c["command"] for c in editor_menu[0]],
                         ["start", "config", "upload", "presets", "help"])
        self.assertEqual(editor_menu[1], {"type": "chat", "chat_id": "2"})

    # ---- 选账号是所有操作的前提 ----

    def _configured_cfg(self):
        plugin = _plugin()
        cfg = json.loads(json.dumps(self.cfg))  # deep copy
        cfg["accounts"][0]["base_schedule"] = full_base_schedule(plugin)
        return cfg

    def test_owner_must_select_account_before_anything(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            bot = make_bot(api, self._configured_cfg(), RolePolicy("1"), td)

            def send(text):
                return send_as(bot, "1", text)

            # 未选账号：除 /select 外全部被拦下
            self.assertIn("请先 /select 选择账号", send("/config"))
            self.assertIn("请先 /select 选择账号", send("/presets"))
            self.assertIn("请先 /select 选择账号", send("/preset use X"))
            self.assertIn("请先 /select 选择账号", send("/invite"))
            self.assertIn("请先 /select 选择账号", send("/publish whatever"))
            bot.handle_message({"from": {"id": "1"}, "chat": {"id": "1", "type": "private"},
                                "document": {"file_name": "x.json", "file_id": "f"}})
            self.assertIn("请先 /select 选择账号", api.sent[-1][1])
            # 拦截时同步刷新菜单（隐藏按钮）
            self.assertEqual([c["command"] for c in api.menus[-1][0]], ["start", "select", "help"])

    def test_select_shows_account_buttons(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            bot = make_bot(api, self._configured_cfg(), RolePolicy("1"), td)
            send_as(bot, "1", "/select")
            text, markup = api.sent[-1][1], api.sent[-1][2]
            self.assertIn("点按钮选择要操作的账号", text)
            buttons = [b for row in markup["inline_keyboard"] for b in row]
            self.assertEqual([(b["text"], b["callback_data"]) for b in buttons],
                             [("官服1", "sel:a1"), ("官服2", "sel:a2")])
            send_as(bot, "1", "/select 1")
            send_as(bot, "1", "/select")
            self.assertIn("当前已选：「官服1」", api.sent[-1][1])

    def test_callback_press_selects_account(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            bot = make_bot(api, self._configured_cfg(), RolePolicy("1"), td)
            cb = {"id": "cb1", "from": {"id": "1"}, "data": "sel:a2",
                  "message": {"chat": {"id": "1", "type": "private"}, "message_id": 7}}
            bot._handle_callback_safe(cb)
            self.assertEqual(api.answers, [("cb1", "已选「官服2」")])
            self.assertEqual(len(api.edits), 1)
            self.assertEqual(api.edits[0][0], "1")
            self.assertEqual(api.edits[0][1], 7)
            self.assertIn("已选「官服2」", api.edits[0][2])
            ready = api.menus[-1][0]  # 选中后菜单展开
            self.assertEqual([c["command"] for c in ready],
                             ["start", "config", "upload", "presets", "publish", "invite", "select", "help"])
            # 持久化 + 重启后仍生效
            self.assertEqual(SelectionStore(Path(td) / "sel.json").get("1"), "a2")
            # /config 跟随按钮选中的账号
            self.assertIn("「官服2」基建排班：未配置批次", send_as(bot, "1", "/config"))

    def test_callback_denied_for_editor_and_stranger(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            bot = make_bot(api, self._configured_cfg(), RolePolicy("1", {"2": "a1"}), td)
            bot._handle_callback_safe({"id": "cb2", "from": {"id": "2"}, "data": "sel:a2",
                                       "message": {"chat": {"id": "2", "type": "private"}, "message_id": 1}})
            self.assertEqual(api.answers, [("cb2", "只有所有者需要选账号")])
            self.assertIsNone(SelectionStore(Path(td) / "sel.json").get("2"))
            api2 = Api()
            bot2 = make_bot(api2, self._configured_cfg(), RolePolicy("1"), td)
            bot2._handle_callback_safe({"id": "cb3", "from": {"id": "9"}, "data": "sel:a1",
                                        "message": {"chat": {"id": "9", "type": "private"}, "message_id": 1}})
            self.assertEqual(api2.answers, [("cb3", "只有所有者需要选账号")])

    def test_callback_unknown_account_and_group_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            bot = make_bot(api, self._configured_cfg(), RolePolicy("1"), td)
            bot._handle_callback_safe({"id": "cb4", "from": {"id": "1"}, "data": "sel:zzz",
                                       "message": {"chat": {"id": "1", "type": "private"}, "message_id": 1}})
            self.assertEqual(api.answers, [("cb4", "该账号已不存在")])
            self.assertIn("请重新 /select", api.sent[-1][1])
            # 群聊里的回调一律忽略
            bot._handle_callback_safe({"id": "cb5", "from": {"id": "1"}, "data": "sel:a1",
                                       "message": {"chat": {"id": "1", "type": "group"}, "message_id": 1}})
            self.assertEqual(api.answers[-1], ("cb5", None))

    def test_updates_subscription_includes_callback_queries(self):
        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"ok": true, "result": []}'

        def opener(req, timeout=None):
            captured["req"] = req
            return _Resp()

        TelegramAPI("123:abc", opener=opener).updates(5)
        body = captured["req"].data.decode("utf-8")
        self.assertIn("callback_query", body)
        self.assertIn("message", body)

    def test_select_persists_across_bot_restart(self):
        with tempfile.TemporaryDirectory() as td:
            sel_path = Path(td) / "sel.json"
            api = Api()
            bot = make_bot(api, self._configured_cfg(), RolePolicy("1"), td, selection=SelectionStore(sel_path))
            send_as(bot, "1", "/select 1")
            api2 = Api()
            bot2 = make_bot(api2, self._configured_cfg(), RolePolicy("1"), td, selection=SelectionStore(sel_path))
            bot2.configure_commands()  # 重启后菜单按已选状态展开
            self.assertEqual([c["command"] for c in api2.menus[-1][0]][0:3],
                             ["start", "config", "upload"])
            self.assertIn("「官服1」基建排班", send_as(bot2, "1", "/config"))

    def test_editor_skips_selection_and_stranger_denied(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            bot = make_bot(api, self._configured_cfg(), RolePolicy("1", {"2": "a1"}), td)
            self.assertIn("编辑者固定绑定账号，无需选择。", send_as(bot, "2", "/select 1"))
            self.assertIn("「官服1」基建排班", send_as(bot, "2", "/config"))
            self.assertIn("账号权限不足", send_as(bot, "9", "/config"))

    def test_config_editor_shows_bound_account_directly(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            self.cfg["accounts"][0]["password"] = "hidden"
            bot = make_bot(api, self._configured_cfg(), RolePolicy("1", {"2": "a1"}), td)
            text = send_as(bot, "2", "/config")
            self.assertIn("官服1", text)
            self.assertIn("▼ 4点批", text)
            self.assertNotIn("hidden", text)

    def test_config_detail_readable_and_secret_free(self):
        plugin = _plugin()
        account = {"id": "a1", "label": "官服1", "password": "hidden", "base_schedule": full_base_schedule(plugin)}
        text = format_account_detail(plugin, account)
        self.assertIn("「官服1」基建排班 ✅启用 · 布局333（贸易3·制造3·发电3）", text)
        self.assertIn("🚁 无人机：制造站1号 · 换班前投放", text)
        self.assertIn("控制中枢：夜莺、闪灵 ＋3自动", text)
        self.assertIn("制造1·赤金：能天使 ＋2自动", text)
        self.assertIn("制造2·赤金：自动", text)
        self.assertIn("贸易1·龙门币订单：自动", text)
        self.assertIn("宿舍1：暮落 ＋4自动", text)
        self.assertIn("宿舍2-4：自动", text)
        self.assertIn("发电站：自动", text)
        self.assertIn("菲亚梅塔：换班前恢复「清流」", text)
        self.assertNotIn("hidden", text)

    def test_format_overview_and_resolve_account_token(self):
        plugin = _plugin()
        cfg = self._configured_cfg()
        self.assertEqual(format_account_overview(1, cfg["accounts"][0]), "1. 官服1 ✅启用 · 333 · 2批")
        self.assertEqual(format_account_overview(2, cfg["accounts"][1]), "2. 官服2（未配置）")
        accounts = cfg["accounts"]
        self.assertEqual(resolve_account_token(accounts, "官服2")["id"], "a2")
        self.assertEqual(resolve_account_token(accounts, "a2")["id"], "a2")
        self.assertEqual(resolve_account_token(accounts, "1")["id"], "a1")
        with self.assertRaises(ValueError): resolve_account_token(accounts, "0")
        with self.assertRaises(ValueError): resolve_account_token(accounts, "不存在")

    # ---- 上传：目标是当前账号，所有者无需 caption ----

    def test_owner_upload_goes_to_selected_account_as_draft(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            fake = FakeAppConfig(self._configured_cfg())
            bot = make_bot(api, fake.cfg, RolePolicy("1"), td, appconfig=fake)
            send_as(bot, "1", "/select 官服1")
            bot.handle_message({"from": {"id": "1"}, "chat": {"id": "1", "type": "private"},
                                "document": {"file_name": "base.json", "file_id": "f"}})
            text = api.sent[-1][1]
            self.assertIn("账号「官服1」：布局 243", text)
            self.assertIn("草稿 ID：", text)
            self.assertIn("/publish", text)
            self.assertFalse(fake.saved)  # 所有者上传只生成草稿，不落盘

    def test_editor_upload_ignores_caption_and_writes_bound_account(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            fake = FakeAppConfig(self.cfg)
            bot = make_bot(api, self.cfg, RolePolicy("1", {"2": "a1"}), td, appconfig=fake)
            bot.handle_message({"from": {"id": 2}, "chat": {"id": 2, "type": "private"}, "caption": "a2",
                                "document": {"file_name": "base.json", "file_id": "f"}})
            self.assertTrue(fake.cfg["accounts"][0]["base_schedule"]["enabled"])
            self.assertFalse(fake.cfg["accounts"][1]["base_schedule"]["enabled"])
            self.assertEqual(len(fake.saved), 1)

    def test_publish_requires_same_selected_account(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            fake = FakeAppConfig(self._configured_cfg())
            bot = make_bot(api, fake.cfg, RolePolicy("1"), td, appconfig=fake)
            send_as(bot, "1", "/select 1")
            bot.handle_message({"from": {"id": "1"}, "chat": {"id": "1", "type": "private"},
                                "document": {"file_name": "base.json", "file_id": "f"}})
            draft_id = api.sent[-1][1].split("草稿 ID：")[1].splitlines()[0].strip()
            # 换账号后不能发布别的账号的草稿
            send_as(bot, "1", "/select 2")
            self.assertIn("该草稿属于账号「官服1」", send_as(bot, "1", "/publish " + draft_id))
            self.assertIn("/select 1", api.sent[-1][1])
            # 选回后再发布成功
            send_as(bot, "1", "/select 1")
            self.assertIn("已发布到账号「官服1」", send_as(bot, "1", "/publish " + draft_id))
            self.assertTrue(fake.saved[0]["accounts"][0]["base_schedule"]["enabled"])

    # ---- 预设：先选账号，再在当前账号上管理 ----

    def _preset_cfg(self):
        plugin = _plugin()
        cfg = json.loads(json.dumps(self._configured_cfg()))
        b = plugin.default_batch("333")
        b["control"] = ["德克萨斯", "", "", "", ""]
        cfg["accounts"][0]["base_schedule_presets"] = [
            {"name": "夜班方案", "schedule": {
                "layout": "333",
                "drones": {"room": "manufacture", "index": 1, "enable": False, "order": "pre"},
                "batches": {"4点": b, "16点": plugin.default_batch("333")}}},
            {"name": "空方案", "schedule": {}},
        ]
        return cfg

    def test_presets_manage_page_of_selected_account(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            bot = make_bot(api, self._preset_cfg(), RolePolicy("1"), td)
            self.assertIn("请先 /select 选择账号", send_as(bot, "1", "/presets"))
            send_as(bot, "1", "/select 1")
            text, markup = send_as(bot, "1", "/presets"), api.sent[-1][2]
            self.assertIn("「官服1」的预设：", text)
            self.assertIn("· 夜班方案（333·2批）", text)
            self.assertIn("· 空方案（空）", text)
            self.assertIn("保存当前排班：/preset save 名称 [覆盖]", text)
            # 每条预设一行按钮：[📄 名称] [▶ 应用] [🗑 删除]
            rows = markup["inline_keyboard"]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][0]["text"], "📄 夜班方案（333·2批）")
            self.assertTrue(rows[0][0]["callback_data"].startswith("preshow:a1:"))
            self.assertTrue(rows[0][1]["callback_data"].startswith("preuse:a1:"))
            self.assertTrue(rows[0][2]["callback_data"].startswith("predel:a1:"))
            self.assertEqual(rows[1][0]["text"], "📄 空方案（空）")
            # 编辑者直接看绑定账号
            api2 = Api()
            bot2 = make_bot(api2, self._preset_cfg(), RolePolicy("1", {"2": "a1"}), td)
            page2, markup2 = send_as(bot2, "2", "/presets"), api2.sent[-1][2]
            self.assertIn("「官服1」的预设：", page2)
            self.assertTrue(markup2["inline_keyboard"][0][0]["callback_data"].startswith("preshow:a1:"))
            self.assertNotIn("官服2", page2)
            self.assertIn("账号权限不足", send_as(bot2, "9", "/presets"))

    def test_preset_buttons_view_apply_delete_flow(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            fake = FakeAppConfig(self._preset_cfg())
            bot = make_bot(api, fake.cfg, RolePolicy("1"), td, appconfig=fake)
            send_as(bot, "1", "/select 1")
            send_as(bot, "1", "/presets")
            row = api.sent[-1][2]["inline_keyboard"][0]
            preshow, preuse, predel = (b["callback_data"] for b in row)
            message = {"chat": {"id": "1", "type": "private"}, "message_id": 9}

            def cb(data):
                bot._handle_callback_safe({"id": "cb-" + data[:12], "from": {"id": "1"},
                                           "data": data, "message": message})

            # 📄 查看 → 原地编辑为预设详情 + 操作按钮
            cb(preshow)
            self.assertIn("「夜班方案」", api.edits[-1][2])
            self.assertIn("预设 · 布局333", api.edits[-1][2])
            btns = api.edits[-1][3]["inline_keyboard"][0]
            self.assertEqual([b["text"] for b in btns], ["▶ 应用", "🗑 删除", "◀ 返回列表"])
            # ▶ 应用 → 写入配置 + 原地显示结果
            cb(preuse)
            self.assertIn("已把预设「夜班方案」应用到账号「官服1」", api.edits[-1][2])
            self.assertTrue(fake.saved and fake.saved[-1]["accounts"][0]["base_schedule"]["enabled"])
            self.assertEqual(fake.saved[-1]["accounts"][0]["base_schedule"]["batches"]["4点"]["control"][0],
                             "德克萨斯")
            # 🗑 删除 → 先确认，再确认才真删
            cb(predel)
            self.assertIn("确认删除预设「夜班方案」", api.edits[-1][2])
            self.assertEqual(len(fake.cfg["accounts"][0]["base_schedule_presets"]), 2)
            confirm = [b["callback_data"] for b in api.edits[-1][3]["inline_keyboard"][0]
                       if b["text"] == "🗑 确认删除"][0]
            cb(confirm)
            self.assertIn("已删除预设「夜班方案」", api.edits[-1][2])
            self.assertEqual([p["name"] for p in fake.cfg["accounts"][0]["base_schedule_presets"]],
                             ["空方案"])
            # ◀ 返回列表 → 重新渲染（只剩一条）
            cb("preback:a1")
            self.assertIn("「官服1」的预设：", api.edits[-1][2])
            self.assertEqual(len(api.edits[-1][3]["inline_keyboard"]), 1)

    def test_preset_buttons_stale_guards(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            fake = FakeAppConfig(self._preset_cfg())
            bot = make_bot(api, fake.cfg, RolePolicy("1"), td, appconfig=fake)
            send_as(bot, "1", "/select 1")
            send_as(bot, "1", "/presets")
            preuse = api.sent[-1][2]["inline_keyboard"][0][1]["callback_data"]
            message = {"chat": {"id": "1", "type": "private"}, "message_id": 5}
            # 换账号后按钮失效：不改配置
            send_as(bot, "1", "/select 2")
            bot._handle_callback_safe({"id": "cbx", "from": {"id": "1"}, "data": preuse, "message": message})
            self.assertEqual(api.answers[-1], ("cbx", "账号已切换"))
            self.assertIn("账号已切换", api.edits[-1][2])
            self.assertFalse(fake.saved)
            # 回到 a1，但预设被删除后按钮也失效
            send_as(bot, "1", "/select 1")
            send_as(bot, "1", "/preset del 夜班方案")
            bot._handle_callback_safe({"id": "cby", "from": {"id": "1"}, "data": preuse, "message": message})
            self.assertEqual(api.answers[-1], ("cby", "预设列表已变化"))
            self.assertIn("预设列表已变化", api.edits[-1][2])

    def test_preset_show_renders_and_errors(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            bot = make_bot(api, self._preset_cfg(), RolePolicy("1"), td)
            send_as(bot, "1", "/select 1")
            text = send_as(bot, "1", "/preset show 夜班方案")
            self.assertIn("预设 · 布局333（贸易3·制造3·发电3）", text)
            self.assertIn("控制中枢：德克萨斯 ＋4自动", text)
            self.assertIn("应用：/preset use 夜班方案", text)
            self.assertIn("删除：/preset del 夜班方案", text)
            self.assertIn("找不到预设「不存在」", send_as(bot, "1", "/preset show 不存在"))
            self.assertIn("夜班方案", api.sent[-1][1])  # 提示里带可用预设名
            self.assertIn("show 查看 · use 应用", send_as(bot, "1", "/preset"))

    def test_preset_names_with_spaces_need_quotes(self):
        with tempfile.TemporaryDirectory() as td:
            plugin = _plugin()
            cfg = self._preset_cfg()
            b = plugin.default_batch("333")
            cfg["accounts"][0]["base_schedule_presets"].append(
                {"name": "333 日常", "schedule": {"layout": "333", "batches": {"4点": b}}})
            api = Api()
            bot = make_bot(api, cfg, RolePolicy("1"), td)
            send_as(bot, "1", "/select 1")
            text = send_as(bot, "1", "/preset show 「333 日常」")
            self.assertIn("预设 · 布局333", text)
            self.assertIn("应用：/preset use 「333 日常」", text)
            self.assertIn("找不到预设「333」", send_as(bot, "1", "/preset show 333 日常"))

    def test_preset_use_applies_with_changes_and_keeps_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            fake = FakeAppConfig(self._preset_cfg())
            bot = make_bot(api, fake.cfg, RolePolicy("1"), td, appconfig=fake)
            send_as(bot, "1", "/select 1")
            fake.cfg["gui_marker"] = "keep-me"
            text = send_as(bot, "1", "/preset use 夜班方案")
            self.assertIn("已把预设「夜班方案」应用到账号「官服1」。", text)
            self.assertIn("覆盖批次：4点、16点", text)
            self.assertIn("变更：", text)
            self.assertIn("基建当前：✅启用（不改启用）", text)
            saved = fake.saved[0]
            self.assertEqual(saved["gui_marker"], "keep-me")  # 基于磁盘最新配置
            bs = saved["accounts"][0]["base_schedule"]
            self.assertTrue(bs["enabled"])  # 启用状态保留
            self.assertEqual(bs["batches"]["4点"]["control"][0], "德克萨斯")
            self.assertFalse(bs["drones"]["enable"])  # 无人机取预设
            self.assertFalse(saved["accounts"][1].get("base_schedule", {}).get("enabled"))

    def test_preset_save_and_delete_with_overwrite_guard(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            fake = FakeAppConfig(self._preset_cfg())
            bot = make_bot(api, fake.cfg, RolePolicy("1"), td, appconfig=fake)
            send_as(bot, "1", "/select 2")  # 官服2：无预设、无排班

            def send(text):
                return send_as(bot, "1", text)

            send("/preset save 当前方案")
            self.assertIn("还没有排班内容", api.sent[-1][1])
            send_as(bot, "1", "/select 1")
            send("/preset save 夜班方案")
            self.assertIn("已有同名预设", api.sent[-1][1])
            self.assertIn("覆盖", api.sent[-1][1])
            send("/preset save 夜班方案 覆盖")
            self.assertIn("已覆盖保存预设「夜班方案」", api.sent[-1][1])
            presets = fake.cfg["accounts"][0]["base_schedule_presets"]
            self.assertEqual(len(presets), 2)  # 覆盖不新增条目
            self.assertEqual(presets[0]["name"], "夜班方案")
            send("/preset save 新方案")
            self.assertIn("已保存预设「新方案」", api.sent[-1][1])
            self.assertIn("应用：/preset use 新方案", api.sent[-1][1])  # 保存后直接给出应用命令
            self.assertEqual(len(fake.cfg["accounts"][0]["base_schedule_presets"]), 3)
            send("/preset del 夜班方案")
            self.assertIn("已删除预设「夜班方案」", api.sent[-1][1])
            self.assertEqual([p["name"] for p in fake.cfg["accounts"][0]["base_schedule_presets"]],
                             ["空方案", "新方案"])
            send("/preset del 夜班方案")
            self.assertIn("找不到预设「夜班方案」", api.sent[-1][1])

    def test_describe_schedule_changes_covers_products_and_drones(self):
        plugin = _plugin()
        cur = plugin.default_base_schedule()
        new = plugin.default_base_schedule()
        new["enabled"] = True
        new["batches"]["4点"]["manufacture"][0]["product"] = "Battle Record"
        new["batches"]["4点"]["trading"][0]["operators"] = ["能天使", "", ""]
        new["drones"] = {"room": "trading", "index": 2, "enable": True, "order": "post"}
        lines = describe_schedule_changes(plugin, cur, new)
        self.assertIn("基建：⛔停用 → ✅启用", lines)
        self.assertIn("无人机：未启用 → 贸易站2号·换班后投放", lines)
        self.assertIn("4点批·制造1 产品：赤金 → 作战记录", lines)
        self.assertIn("4点批·贸易1：自动 → 能天使 ＋2自动", lines)
        self.assertEqual(describe_schedule_changes(plugin, cur, cur), [])

    # ---- 写路径：永远基于磁盘最新配置 ----

    def test_save_uses_fresh_config_from_disk(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            fake = FakeAppConfig(self.cfg)
            bot = make_bot(api, self.cfg, RolePolicy("1", {"2": "a1"}), td, appconfig=fake)
            fake.cfg["gui_marker"] = "keep-me"  # 机器人启动后 GUI 改过磁盘配置
            bot.handle_message({"from": {"id": 2}, "chat": {"id": 2, "type": "private"},
                                "document": {"file_name": "base.json", "file_id": "f"}})
            self.assertEqual(len(fake.saved), 1)
            saved = fake.saved[0]
            self.assertEqual(saved["gui_marker"], "keep-me")  # GUI 的修改没有被快照覆盖
            self.assertTrue(saved["accounts"][0]["base_schedule"]["enabled"])
            self.assertFalse(saved["accounts"][1]["base_schedule"]["enabled"])
            self.assertTrue(bot.cfg["accounts"][0]["base_schedule"]["enabled"])

    # ---- 邀请：绑定码指向当前选中账号 ----

    def test_invite_targets_selected_account(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            invites = InviteStore(Path(td) / "invites.json")
            bot = make_bot(api, self._configured_cfg(), RolePolicy("1"), td, invites=invites)
            self.assertIn("请先 /select 选择账号", send_as(bot, "1", "/invite"))
            send_as(bot, "1", "/select 2")
            text = send_as(bot, "1", "/invite")
            self.assertIn("绑定账号「官服2」", text)
            code = text.splitlines()[-1]
            self.assertEqual(invites.redeem(code, "5"), "a2")

    def test_bindings_restore_after_restart(self):
        with tempfile.TemporaryDirectory() as td:
            store = InviteStore(Path(td) / "invites.json")
            code = store.create("a1", ttl=600)
            store.redeem(code, "2")
            policy = RolePolicy("1")
            restore_bindings(policy, store)
            self.assertEqual(policy.editor_accounts.get("2"), "a1")
            # CLI 显式指定的绑定优先于历史记录
            store2 = InviteStore(Path(td) / "invites2.json")
            code2 = store2.create("a1", ttl=600)
            store2.redeem(code2, "9")
            policy2 = RolePolicy("1", {"9": "a2"})
            restore_bindings(policy2, store2)
            self.assertEqual(policy2.editor_accounts.get("9"), "a2")

    # ---- 长轮询：崩溃兜底 + offset 持久化 ----

    def test_poll_survives_errors_and_persists_offset(self):
        class FlakyApi(Api):
            def __init__(self):
                super().__init__()
                self.batches = []
                self.calls = 0

            def updates(self, offset):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("network down")
                pending, self.batches = self.batches, []
                return pending

        with tempfile.TemporaryDirectory() as td:
            api = FlakyApi()
            state = PollState(Path(td) / "poll_state.json")
            bot = make_bot(api, self.cfg, RolePolicy("1"), td, state=state)
            offset, ok = bot._poll_once(None)
            self.assertEqual((offset, ok), (None, False))
            self.assertIsNone(state.load())
            api.batches = [
                {"update_id": 100, "message": {"from": {"id": "1"}, "chat": {"id": "1", "type": "private"}, "text": "/start"}},
                {"update_id": 101, "message": {"from": {"id": "1"}, "chat": {"id": "1", "type": "private"}, "text": "闲聊"}},
            ]
            offset, ok = bot._poll_once(None)
            self.assertEqual((offset, ok), (102, True))
            self.assertEqual(state.load(), 102)
            self.assertIn("基建机器人已就绪", api.sent[-1][1])

    def test_message_failure_is_caught_and_reported(self):
        class FailOnceApi(Api):
            def __init__(self):
                super().__init__()
                self.failed = False

            def send(self, chat, text):
                if not self.failed:
                    self.failed = True
                    raise RuntimeError("boom")
                self.sent.append((chat, text))

        with tempfile.TemporaryDirectory() as td:
            api = FailOnceApi()
            bot = make_bot(api, self.cfg, RolePolicy("1"), td)
            bot._handle_update_safe({"update_id": 1, "message": {
                "from": {"id": "1"}, "chat": {"id": "1", "type": "private"}, "text": "/start"}})
            self.assertIn("处理失败", api.sent[-1][1])

    def test_private_chat_only_and_unknown_command_hint(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            bot = make_bot(api, self.cfg, RolePolicy("1"), td)
            bot.handle_message({"from": {"id": "1"}, "chat": {"id": "1", "type": "group"}, "text": "/config"})
            self.assertEqual(api.sent, [])
            bot.handle_message({"from": {"id": "1"}, "chat": {"id": "1", "type": "private"}, "text": "你好"})
            self.assertEqual(api.sent, [])
            self.assertIn("未知命令", send_as(bot, "1", "/foo"))
            self.assertIn("/help", api.sent[-1][1])

    def test_help_is_role_scoped_and_start_shows_role(self):
        with tempfile.TemporaryDirectory() as td:
            api = Api()
            bot = make_bot(api, self._preset_cfg(), RolePolicy("1", {"2": "a1"}), td)

            def send(uid, text):
                return send_as(bot, uid, text)

            owner_help = send("1", "/help")
            self.assertIn("所有者：先 /select 选账号", owner_help)  # 未选账号时提示
            self.assertIn("/select 选账号", owner_help)
            self.assertIn("/publish 草稿ID 发布草稿", owner_help)
            self.assertIn("/preset use 名称 应用预设", owner_help)
            self.assertIn("/preset save 名称 [覆盖] 存为预设", owner_help)
            send("1", "/select 1")
            owner_help2 = send("1", "/help")
            self.assertIn("所有者：当前「官服1」", owner_help2)
            self.assertIn("所有操作只针对该账号", owner_help2)
            editor_help = send("2", "/help")
            self.assertIn("编辑者：绑定「官服1」", editor_help)
            self.assertIn("/bind 绑定码 绑定账号", editor_help)
            self.assertNotIn("/invite", editor_help)
            stranger_help = send("9", "/help")
            self.assertIn("未绑定", stranger_help)
            start_text = send("2", "/start")
            self.assertIn("基建机器人已就绪", start_text)
            self.assertIn("编辑者：已绑定「官服1」", start_text)
            self.assertIn("/help", start_text)
            owner_start = send("1", "/start")
            self.assertIn("所有者：当前「官服1」", owner_start)

    def test_poll_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            state = PollState(Path(td) / "sub" / "poll_state.json")
            self.assertIsNone(state.load())
            state.save(12345)
            self.assertEqual(state.load(), 12345)

    def test_selection_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            store = SelectionStore(Path(td) / "sub" / "sel.json")
            self.assertIsNone(store.get("1"))
            store.set("1", "a1")
            store.set("2", "a2")
            self.assertEqual(store.get("1"), "a1")
            self.assertEqual(store.get("2"), "a2")
            store.set("1", None)
            self.assertIsNone(store.get("1"))
            self.assertEqual(store.get("2"), "a2")


def _plugin():
    from remote_control.telegram_base_schedule import _load_base_plugin
    return _load_base_plugin(PROJECT)


if __name__ == "__main__": unittest.main()
