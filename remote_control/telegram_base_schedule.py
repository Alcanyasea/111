"""Telegram-only precise base-schedule editor.

This module never starts or stops MAA.  It accepts only base-schedule JSON,
creates a local draft, and lets the owner publish it into config.json.
"""
import copy
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class RolePolicy:
    def __init__(self, owner_id, editor_accounts=None):
        self.owner_id = str(owner_id)
        self.editor_accounts = {str(k): str(v) for k, v in (editor_accounts or {}).items()}

    def is_owner(self, user_id):
        return str(user_id) == self.owner_id

    def bound_account(self, user_id):
        """Return the one account assigned to an editor, or None."""
        return self.editor_accounts.get(str(user_id))

    def account_for(self, user_id, account_id):
        user_id, account_id = str(user_id), str(account_id)
        if self.is_owner(user_id):
            return account_id
        if self.editor_accounts.get(user_id) == account_id:
            return account_id
        raise PermissionError("账号权限不足")


class SelectionStore:
    """每个用户当前选中的账号（user_id → account_id），本地持久化，重启不丢。"""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, value):
        tmp = self.path.with_suffix(".tmp-%d" % os.getpid())
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, user_id):
        return self._read().get(str(user_id))

    def set(self, user_id, account_id):
        data = self._read()
        if account_id is None:
            data.pop(str(user_id), None)
        else:
            data[str(user_id)] = str(account_id)
        self._write(data)


def _load_base_plugin(project_root):
    plugin_dir = str(Path(project_root) / "plugins" / "base_schedule")
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
    import base_schedule as plugin
    return plugin


def build_schedule_patch(cfg, account_id, raw_bytes, project_root=None):
    try:
        doc = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("文件不是有效的 UTF-8 JSON") from exc
    if not isinstance(doc, dict):
        raise ValueError("只接受基建排班 JSON")
    forbidden = {"accounts", "password", "username", "token", "uid", "paths", "notify", "appearance"}
    if forbidden.intersection(doc):
        raise ValueError("不能上传整份配置或含账号机密的文件")
    plugin = _load_base_plugin(project_root or Path(__file__).resolve().parents[1])
    result = plugin.convert_import_document(cfg, doc)
    account = next((a for a in cfg.get("accounts") or [] if str(a.get("id")) == str(account_id)), None)
    if not isinstance(account, dict):
        raise ValueError("账号不存在")
    current = copy.deepcopy(account.get("base_schedule") or {})
    current["enabled"] = True
    current["layout"] = result["layout"]
    current["batches"] = result["batches"]
    if result.get("drones") is not None:
        current["drones"] = result["drones"]
    label = account.get("label") or account_id
    summary = "账号「%s」：布局 %s，更新 %d 个批次" % (label, result["layout"], len(result["batches"]))
    return {"base_schedule": current}, summary, list(result.get("notes") or [])


# ---- 手机端可读排版（/config 与上传回执共用，只含干员/产品名，不含机密） ----

ROOM_NAMES = {
    "control": "控制中枢",
    "meeting": "会客室",
    "manufacture": "制造站",
    "trading": "贸易站",
    "power": "发电站",
    "dormitory": "宿舍",
    "office": "办公室",
    "processing": "加工站",
}

PRODUCT_NAMES = {
    "Pure Gold": "赤金",
    "Originium Shard": "原石碎片",
    "Battle Record": "作战记录",
    "LMD": "龙门币订单",
    "Orundum": "合成玉订单",
}

LAYOUT_NAMES = {"333": "贸易3·制造3·发电3", "243": "贸易2·制造4·发电3"}


def _display_product(product):
    return PRODUCT_NAMES.get(str(product), str(product))


def _ops_text(ops):
    """槽位干员列表 → 「夜莺、闪灵 ＋3自动」/ 全空 → 「自动」。"""
    filled = [str(o).strip() for o in (ops or []) if str(o).strip()]
    empty = len(list(ops or [])) - len(filled)
    if not filled:
        return "自动"
    text = "、".join(filled)
    if empty > 0:
        text += " ＋%d自动" % empty
    return text


def _norm_layout(layout):
    return "243" if layout in ("423", "243") else "333"


def format_account_overview(index, account):
    """账号列表里的一行：编号 + 名称 + 启用状态 + 布局 + 批次数。"""
    label = str(account.get("label") or account.get("id") or "")
    bs = account.get("base_schedule") if isinstance(account.get("base_schedule"), dict) else {}
    batches = bs.get("batches") if isinstance(bs.get("batches"), dict) else {}
    if not batches:
        return "%d. %s（未配置）" % (index, label)
    state = "✅启用" if bs.get("enabled") else "⛔停用"
    return "%d. %s %s · %s · %d批" % (index, label, state, _norm_layout(bs.get("layout")), len(batches))


def _dorm_lines(dorms):
    """宿舍 4 行：填了干员的逐行显示，末尾连号空宿舍折叠成一行。"""
    filled = [(i + 1, row) for i, row in enumerate(dorms or [])
              if any(str(o).strip() for o in (row or []))]
    if not filled:
        return ["宿舍1-4：自动"]
    lines = ["宿舍%d：%s" % (idx, _ops_text(row)) for idx, row in filled]
    last = filled[-1][0]
    if last < len(dorms or []):
        rest = len(dorms) - last
        lines.append(("宿舍%d-4：自动" % (last + 1)) if rest > 1 else ("宿舍%d：自动" % (last + 1)))
    return lines


def _render_schedule_body(bs):
    """normalize 后的 base_schedule → 批次排版行（无人机 + 各批次，不含标题头）。"""
    lines = []
    drones = bs.get("drones") or {}
    if drones.get("enable"):
        lines.append("🚁 无人机：%s%d号 · %s投放" % (
            "制造站" if drones.get("room") == "manufacture" else "贸易站",
            drones.get("index") or 1,
            "换班前" if drones.get("order") == "pre" else "换班后"))
    for name, b in bs["batches"].items():
        lines.append("")
        lines.append("▼ %s批" % name)
        lines.append("控制中枢：%s" % _ops_text(b["control"]))
        lines.append("会客室：%s" % _ops_text(b["meeting"]))
        for i, st in enumerate(b["manufacture"], 1):
            lines.append("制造%d·%s：%s" % (i, _display_product(st["product"]), _ops_text(st["operators"])))
        for i, st in enumerate(b["trading"], 1):
            lines.append("贸易%d·%s：%s" % (i, _display_product(st["product"]), _ops_text(st["operators"])))
        power_filled = [row for row in b["power"] if any(str(o).strip() for o in row)]
        if power_filled:
            for i, row in enumerate(b["power"], 1):
                if any(str(o).strip() for o in row):
                    lines.append("发电%d：%s" % (i, _ops_text(row)))
        else:
            lines.append("发电站：自动")
        lines.extend(_dorm_lines(b["dormitory"]))
        lines.append("办公室：%s" % _ops_text(b["office"]))
        lines.append("加工站：%s" % _ops_text(b["processing"]))
        fia = b.get("fiammetta") or {}
        if fia.get("enable") and fia.get("target"):
            lines.append("菲亚梅塔：换班前恢复「%s」" % fia["target"])
    return lines


def format_account_detail(plugin, account):
    """单个账号的基建排班全文（按批次/房间），手机可读。"""
    label = str(account.get("label") or account.get("id") or "")
    bs = account.get("base_schedule") if isinstance(account.get("base_schedule"), dict) else {}
    batches_src = bs.get("batches") if isinstance(bs.get("batches"), dict) else {}
    if not batches_src:
        return "「%s」基建排班：未配置批次。\n发送排班 JSON（见 /upload）后自动生成。" % label
    bs = plugin.normalize(bs, list(batches_src.keys()))
    lines = ["「%s」基建排班 %s · 布局%s（%s）" % (
        label,
        "✅启用" if bs.get("enabled") else "⛔停用",
        bs["layout"], LAYOUT_NAMES.get(bs["layout"], ""))]
    lines.extend(_render_schedule_body(bs))
    return "\n".join(lines)


def format_preset_detail(plugin, schedule):
    """预设 schedule（layout/drones/batches）的排版全文，口径与账号详情一致。"""
    ps = schedule if isinstance(schedule, dict) else {}
    batches = ps.get("batches") if isinstance(ps.get("batches"), dict) else {}
    if not batches:
        return "（该预设没有排班内容）"
    bs = plugin.normalize(
        {"layout": ps.get("layout"), "drones": ps.get("drones"), "batches": batches},
        list(batches.keys()))
    lines = ["预设 · 布局%s（%s）" % (bs["layout"], LAYOUT_NAMES.get(bs["layout"], ""))]
    lines.extend(_render_schedule_body(bs))
    return "\n".join(lines)


def preset_summary(preset):
    """单个预设的摘要：「名称（布局·N批）」或「名称（空）」。"""
    if not isinstance(preset, dict):
        return ""
    sched = preset.get("schedule") if isinstance(preset.get("schedule"), dict) else {}
    batches = sched.get("batches") if isinstance(sched.get("batches"), dict) else {}
    name = str(preset.get("name") or "").strip()
    if batches:
        return "%s（%s·%d批）" % (name, _norm_layout(sched.get("layout")), len(batches))
    return "%s（空）" % name


def split_preset_args(rest):
    """预设子命令参数 → (名称, 其余 token 列表)。

    控制台的预设名允许空格（如「333 日常」），名称可用「」或引号包住；
    未加引号时取第一个 token。
    """
    rest = str(rest).strip()
    for q_open, q_close in (("「", "」"), ('"', '"'), ("'", "'")):
        if rest.startswith(q_open):
            end = rest.find(q_close, 1)
            if end > 0:
                return rest[1:end].strip(), rest[end + 1:].split()
    parts = rest.split()
    return ((parts[0] if parts else ""), parts[1:])


def find_preset(account, name):
    """按名称精确查找账号预设；找不到返回 None。"""
    want = str(name).strip()
    items = account.get("base_schedule_presets")
    for p in (items if isinstance(items, list) else []):
        if isinstance(p, dict) and str(p.get("name") or "").strip() == want:
            return p
    return None


def preset_hash(name):
    """预设名 → 8 位十六进制摘要（按钮 callback 数据放名会超 64 字节限制）。"""
    return hashlib.sha256(str(name).strip().encode("utf-8")).hexdigest()[:8]


def find_preset_by_hash(account, digest):
    """按名字摘要查找预设；名字改过/预设删了则找不到。"""
    items = account.get("base_schedule_presets")
    for p in (items if isinstance(items, list) else []):
        if isinstance(p, dict) and preset_hash(p.get("name") or "") == str(digest):
            return p
    return None


def preset_names(account):
    items = account.get("base_schedule_presets")
    return [str(p.get("name") or "") for p in (items if isinstance(items, list) else [])
            if isinstance(p, dict) and str(p.get("name") or "").strip()]


def make_preset_schedule(bs):
    """账号当前 base_schedule → 预设的 schedule（layout/drones/batches 深拷贝）。"""
    if not isinstance(bs, dict) or not isinstance(bs.get("batches"), dict) or not bs["batches"]:
        return None
    return {
        "layout": bs.get("layout"),
        "drones": copy.deepcopy(bs.get("drones") or {}),
        "batches": copy.deepcopy(bs["batches"]),
    }


def apply_preset_schedule(plugin, account, preset, batch_names):
    """按控制台同语义把预设填入账号 base_schedule。

    布局/无人机取预设；批次按名字匹配覆盖（兼容旧键），预设里没有的批次保留
    当前内容；启用状态不变。返回 (新 base_schedule, matched, kept)。
    """
    ps = preset.get("schedule") if isinstance(preset, dict) else None
    if not isinstance(ps, dict) or not isinstance(ps.get("batches"), dict) or not ps["batches"]:
        raise ValueError("该预设没有排班内容")
    current = account.get("base_schedule") if isinstance(account.get("base_schedule"), dict) else {}
    cur_batches = current.get("batches") if isinstance(current.get("batches"), dict) else {}
    layout = _norm_layout(ps.get("layout"))
    names = list(batch_names) if batch_names else list(cur_batches.keys())
    merged, matched, kept = {}, [], []
    for b in names:
        source = plugin.batch_data(ps["batches"], b)
        if source is not None:
            merged[b] = copy.deepcopy(source)
            matched.append(b)
        else:
            cur = plugin.batch_data(cur_batches, b)
            if cur is None:
                cur = plugin.default_batch(layout)
            merged[b] = copy.deepcopy(cur)
            kept.append(b)
    out = plugin.normalize({
        "enabled": bool(current.get("enabled")),
        "layout": layout,
        "drones": ps.get("drones") if isinstance(ps.get("drones"), dict) else {},
        "batches": merged,
    }, names)
    return out, matched, kept


def _batch_room_units(batch):
    """批次 → [(房间名, 干员文本)]，与详情页同口径，供变更对比。"""
    units = [
        (ROOM_NAMES["control"], _ops_text(batch["control"])),
        (ROOM_NAMES["meeting"], _ops_text(batch["meeting"])),
    ]
    for i, st in enumerate(batch["manufacture"], 1):
        units.append(("制造%d" % i, _ops_text(st["operators"])))
    for i, st in enumerate(batch["trading"], 1):
        units.append(("贸易%d" % i, _ops_text(st["operators"])))
    for i, row in enumerate(batch["power"], 1):
        units.append(("发电%d" % i, _ops_text(row)))
    for i, row in enumerate(batch["dormitory"], 1):
        units.append(("宿舍%d" % i, _ops_text(row)))
    units.append((ROOM_NAMES["office"], _ops_text(batch["office"])))
    units.append((ROOM_NAMES["processing"], _ops_text(batch["processing"])))
    fia = batch.get("fiammetta") or {}
    units.append(("菲亚梅塔", ("恢复「%s」" % fia["target"]) if fia.get("enable") and fia.get("target") else "未启用"))
    return units


def _drones_text(drones):
    if not drones.get("enable"):
        return "未启用"
    return "%s%d号·%s投放" % (
        "制造站" if drones.get("room") == "manufacture" else "贸易站",
        drones.get("index") or 1,
        "换班前" if drones.get("order") == "pre" else "换班后")


def describe_schedule_changes(plugin, current_bs, new_bs):
    """两份 base_schedule 的差异行（含产品/无人机/启停/布局），手机可读。"""
    cur = plugin.normalize(current_bs if isinstance(current_bs, dict) else {})
    new = plugin.normalize(new_bs if isinstance(new_bs, dict) else {})
    lines = []
    if bool(cur.get("enabled")) != bool(new.get("enabled")):
        lines.append("基建：⛔停用 → ✅启用" if new.get("enabled") else "基建：✅启用 → ⛔停用")
    if cur.get("layout") != new.get("layout"):
        lines.append("布局：%s → %s" % (cur.get("layout"), new.get("layout")))
    cur_drones = cur.get("drones") or {}
    new_drones = new.get("drones") or {}
    if (bool(cur_drones.get("enable")), cur_drones.get("room"), cur_drones.get("index"), cur_drones.get("order")) != \
       (bool(new_drones.get("enable")), new_drones.get("room"), new_drones.get("index"), new_drones.get("order")):
        lines.append("无人机：%s → %s" % (_drones_text(cur_drones), _drones_text(new_drones)))
    for name, new_batch in new.get("batches", {}).items():
        cur_batch = (cur.get("batches") or {}).get(name)
        if cur_batch is None:
            lines.append("新增批次：%s批" % name)
            continue
        cur_units = dict(_batch_room_units(cur_batch))
        for room, new_text in _batch_room_units(new_batch):
            if room.startswith(("制造", "贸易")):
                idx = int("".join(ch for ch in room if ch.isdigit()) or 0)
                key = "manufacture" if room.startswith("制造") else "trading"
                cur_list = cur_batch[key]
                if idx > len(cur_list):
                    lines.append("%s批·%s：新增" % (name, room))
                    continue
                # 产品变化单独成行（站号相同但配方变了）
                if cur_list[idx - 1]["product"] != new_batch[key][idx - 1]["product"]:
                    lines.append("%s批·%s 产品：%s → %s" % (
                        name, room, _display_product(cur_list[idx - 1]["product"]),
                        _display_product(new_batch[key][idx - 1]["product"])))
            cur_text = cur_units.get(room)
            if cur_text != new_text:
                lines.append("%s批·%s：%s → %s" % (name, room, cur_text, new_text))
    return lines


def resolve_account_token(accounts, token):
    """按唯一名称 → 精确 id → 1 起序号 匹配账号；失败抛带列表提示的 ValueError。"""
    want = str(token).strip()
    if not want:
        raise ValueError("请填写账号编号或名称。发送 /config 查看账号列表。")
    label_hits = [a for a in accounts if str(a.get("label") or "").strip() == want]
    if len(label_hits) > 1:
        raise ValueError("账号名称「%s」对应多个账号，请改用编号。" % want)
    if label_hits:
        return label_hits[0]
    id_hits = [a for a in accounts if str(a.get("id")) == want]
    if id_hits:
        return id_hits[0]
    if want.isdigit() and int(want) >= 1 and int(want) <= len(accounts):
        return accounts[int(want) - 1]
    raise ValueError("找不到账号「%s」。发送 /config 查看账号列表。" % want)


class PollState:
    """长轮询 offset 的本地持久化：重启后从上次位置继续，不重放旧消息。"""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return int(value.get("offset"))
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def save(self, offset):
        tmp = self.path.with_suffix(".tmp-%d" % os.getpid())
        tmp.write_text(json.dumps({"offset": int(offset)}), encoding="utf-8")
        os.replace(tmp, self.path)


class DraftStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, value):
        tmp = self.path.with_suffix(".tmp-%d" % os.getpid())
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def create(self, user_id, account_id, patch, summary):
        drafts = self._read()
        draft_id = secrets.token_urlsafe(8)
        drafts[draft_id] = {"id": draft_id, "user_id": str(user_id), "account_id": str(account_id), "patch": patch, "summary": summary, "created_at": int(time.time()), "status": "draft"}
        self._write(drafts)
        return draft_id

    def take(self, draft_id, user_id, owner_id):
        if str(user_id) != str(owner_id):
            raise PermissionError("只有所有者可以发布配置")
        drafts = self._read(); draft = drafts.get(str(draft_id))
        if not draft or draft.get("status") != "draft":
            raise ValueError("草稿不存在或已发布")
        draft["status"] = "published"; draft["published_at"] = int(time.time()); drafts[str(draft_id)] = draft; self._write(drafts)
        return draft

    def peek(self, draft_id):
        """只读查看草稿（不消费、不校验权限）。"""
        return self._read().get(str(draft_id))


class InviteStore:
    def __init__(self, path):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self):
        if not self.path.exists(): return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8")); return value if isinstance(value, dict) else {}
        except (OSError, ValueError): return {}

    def _write(self, value):
        tmp = self.path.with_suffix(".tmp-%d" % os.getpid()); tmp.write_text(json.dumps(value), encoding="utf-8"); os.replace(tmp, self.path)

    def create(self, account_id, ttl=600):
        invites = self._read(); code = secrets.token_urlsafe(6).upper(); invites[code] = {"account_id": str(account_id), "expires_at": int(time.time()) + int(ttl), "used": False}; self._write(invites); return code

    def redeem(self, code, user_id):
        invites = self._read(); item = invites.get(str(code).strip().upper())
        if not item or item.get("used") or int(item.get("expires_at", 0)) < int(time.time()): raise ValueError("绑定码无效或已过期")
        item["used"] = True; item["user_id"] = str(user_id); invites[str(code).strip().upper()] = item; self._write(invites); return item["account_id"]

    def bindings(self):
        """已兑换的绑定码 → {user_id: account_id}，重启后据此恢复编辑者绑定。"""
        out = {}
        for item in self._read().values():
            if isinstance(item, dict) and item.get("used") and item.get("user_id"):
                out[str(item["user_id"])] = str(item.get("account_id"))
        return out


class TelegramAPI:
    def __init__(self, token, opener=urlopen):
        self.base = "https://api.telegram.org/bot" + token
        self.opener = opener

    def call(self, method, data=None, timeout=35):
        body = urlencode(data or {}).encode()
        req = Request(self.base + "/" + method, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with self.opener(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(payload.get("description", "Telegram API error"))
        return payload.get("result")

    def send(self, chat_id, text, reply_markup=None):
        data = {"chat_id": str(chat_id), "text": text}
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self.call("sendMessage", data)

    def answer_callback_query(self, callback_query_id, text=None):
        data = {"callback_query_id": str(callback_query_id)}
        if text:
            data["text"] = text
        return self.call("answerCallbackQuery", data)

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        data = {"chat_id": str(chat_id), "message_id": int(message_id), "text": text}
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self.call("editMessageText", data)

    def set_my_commands(self, commands, scope=None):
        data = {"commands": json.dumps(commands, ensure_ascii=False)}
        if scope is not None:
            data["scope"] = json.dumps(scope, ensure_ascii=False)
        return self.call("setMyCommands", data)

    def updates(self, offset=None):
        data = {"timeout": 25, "allowed_updates": json.dumps(["message", "callback_query"])}
        if offset is not None: data["offset"] = offset
        return self.call("getUpdates", data, timeout=35)

    def download_document(self, file_id):
        info = self.call("getFile", {"file_id": file_id})
        token = self.base.split("/bot", 1)[1]
        with self.opener("https://api.telegram.org/file/bot%s/%s" % (token, info["file_path"]), timeout=35) as response:
            return response.read()
