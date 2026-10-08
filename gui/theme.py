# -*- coding: utf-8 -*-
"""配色与设计令牌。

明亮主题 = 暖雾灰（B），暗夜主题 = 暮色深灰（D），
由 apply(name) 把对应调色板写入模块级同名变量。

即时换肤：控件的配色样式不直接 setStyleSheet，而是通过 bind(widget, qss_fn)
登记「样式配方」——apply() 末尾统一重套全部配方，窗口不销毁、不重建。
未绑定的动态配色（paintEvent / 富文本里读模块变量的）在主题切换后由
主窗口触发一次全量重画 / 页面刷新，读取的已是新调色板。

立体感公式：背景深一档、卡片亮一档 + 发丝描边 + 柔和投影
（投影参数在 widgets.Card.apply_shadow，两套主题共用）。
"""
import weakref

# ---- 与主题无关的常量 ----

# 字体：Windows 上最接近 SF Pro 的组合；中文回落微软雅黑
FONT_FAMILY = '"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif'
FONT_MONO = '"Cascadia Mono", Consolas, "Courier New", monospace'

# 圆角：卡片 12 / 按钮与输入 8 / 内嵌行 6（对应 macOS 12pt、8pt 层级）
RADIUS_CARD = 12
RADIUS_BTN = 8
RADIUS_ROW = 6

# 控件规格：macOS 常规按钮高度 32~36，输入框 30
BTN_H = 34
BTN_H_SM = 30
INPUT_H = 32

# 卡片内边距与卡片间距（8pt 栅格）
CARD_PAD = 24
GAP = 16

# 深色日志视图（两种主题下日志都保持深色终端观感）
LOG_BG = "#14171c"
LOG_FG = "#c8d0da"
LOG_TS = "#5d6a79"
LOG_OK = "#aeb6bf"
LOG_ERR = "#ffffff"
LOG_HEAD = "#d0d6dd"

# 剩余图标渐变色统一为灰阶（窗口图标用）
ACCENT_O1 = ("#3d434b", "#1d2025")
ACCENT_O2 = ("#4b515a", "#262a30")
ACCENT_B = ("#5b6470", "#2f343b")

# 取点覆盖层
OVERLAY_BG = "rgba(9, 11, 15, 0.94)"

# ---- 调色板 ----

# 明亮：暖雾灰（米白纸面感，柔和暖调）
_LIGHT = {
    "BG": "#ecebe7",
    "CARD": "#fbfaf8",
    "ROW_INSET": "#f1efeb",
    "BORDER": "#e0ddd8",
    "SEP": "rgba(0, 0, 0, 0.08)",          # 分隔线 QSS
    "HAIRLINE": (0, 0, 0, 22),             # 卡片描边 (r, g, b, a)
    "TEXT": "#2e2c29",
    "TEXT_2": "#6d6860",
    "TEXT_3": "#a5a099",
    "ACCENT": "#5a554d",
    "SWITCH_ON": "#5a554d",
    "SWITCH_ON_DARK": "#b5afa5",
    "OK": "#33302b", "OK_TINT": "#edebe6",
    "RUN": "#5a554d", "RUN_TINT": "#e9e6e1",
    "WAIT": "#6d6860", "WAIT_TINT": "#f2f0ec",
    "ERR": "#262421", "ERR_TINT": "#ece9e5",
    "PILL_FAIL_FG": "#ffffff",             # 失败徽章前景（底色用 ERR）
    # token 失效提醒专用红（仪表盘账号卡片标红；整套装色刻意去饱和，
    # 这是唯一的彩色，专用于「必须人工处理」的提醒）
    "ALERT": "#c0392b", "PILL_ALERT_FG": "#ffffff",
    # ghost 按钮
    "BTN_BG": "rgba(255, 255, 255, 0.55)",
    "BTN_BG_HOVER": "rgba(255, 255, 255, 0.78)",
    "BTN_BG_PRESSED": "rgba(255, 255, 255, 0.40)",
    "BTN_BORDER": "rgba(0, 0, 0, 0.10)",
    "BTN_FG": "#2e2c29",
    "BTN_DISABLED_FG": "rgba(0, 0, 0, 0.28)",
    "BTN_DISABLED_BG": "rgba(255, 255, 255, 0.25)",
    "BTN_DISABLED_BORDER": "rgba(0, 0, 0, 0.05)",
    # 主按钮（深暖灰实底）
    "PRIMARY_BG": "#5a554d", "PRIMARY_FG": "#ffffff",
    "PRIMARY_HOVER": "#6b665d", "PRIMARY_PRESSED": "#4a463f",
    "PRIMARY_DISABLED_BG": "rgba(90, 85, 77, 0.35)",
    "PRIMARY_DISABLED_FG": "rgba(255, 255, 255, 0.75)",
    # 圆角数字徽章渐变
    "BADGE_TOP": "#6b665c", "BADGE_BOTTOM": "#3a3733",
    # 通知条 / 弹窗描边
    "INFOBAR_BG": "#f0efeb",
    "POP_BORDER": "rgba(0, 0, 0, 0.10)",
    # 滚动条
    "SCROLLBAR_HANDLE": "rgba(0, 0, 0, 0.18)",
    "SCROLLBAR_HANDLE_HOVER": "rgba(0, 0, 0, 0.32)",
    "SCROLL_HANDLE_COLOR": (0, 0, 0, 46),
}

# 暗夜：暮色深灰（柔和深灰，卡片比背景亮一档）
_DARK = {
    "BG": "#1e2023",
    "CARD": "#292c30",
    "ROW_INSET": "#232629",
    "BORDER": "#3a3e44",
    "SEP": "rgba(255, 255, 255, 0.08)",
    "HAIRLINE": (255, 255, 255, 22),
    "TEXT": "#e7e9ec",
    "TEXT_2": "#a9aeb6",
    "TEXT_3": "#7c828b",
    "ACCENT": "#9aa2ac",
    "SWITCH_ON": "#9aa2ac",
    "SWITCH_ON_DARK": "#4b515a",
    "OK": "#c9ced4", "OK_TINT": "#33373c",
    "RUN": "#b7bec7", "RUN_TINT": "#3a3f45",
    "WAIT": "#a9aeb6", "WAIT_TINT": "#2f3237",
    "ERR": "#ffffff", "ERR_TINT": "#3a3d42",
    "PILL_FAIL_FG": "#1e2023",
    "ALERT": "#e2635a", "PILL_ALERT_FG": "#2b1512",
    "BTN_BG": "rgba(255, 255, 255, 0.08)",
    "BTN_BG_HOVER": "rgba(255, 255, 255, 0.13)",
    "BTN_BG_PRESSED": "rgba(255, 255, 255, 0.05)",
    "BTN_BORDER": "rgba(255, 255, 255, 0.14)",
    "BTN_FG": "#e7e9ec",
    "BTN_DISABLED_FG": "rgba(255, 255, 255, 0.28)",
    "BTN_DISABLED_BG": "rgba(255, 255, 255, 0.04)",
    "BTN_DISABLED_BORDER": "rgba(255, 255, 255, 0.06)",
    "PRIMARY_BG": "#9aa2ac", "PRIMARY_FG": "#202327",
    "PRIMARY_HOVER": "#a8b0b9", "PRIMARY_PRESSED": "#8a929c",
    "PRIMARY_DISABLED_BG": "rgba(154, 162, 172, 0.25)",
    "PRIMARY_DISABLED_FG": "rgba(32, 35, 39, 0.55)",
    "BADGE_TOP": "#6a717b", "BADGE_BOTTOM": "#3f444b",
    "INFOBAR_BG": "#3a3e45",
    "POP_BORDER": "rgba(255, 255, 255, 0.10)",
    "SCROLLBAR_HANDLE": "rgba(255, 255, 255, 0.22)",
    "SCROLLBAR_HANDLE_HOVER": "rgba(255, 255, 255, 0.38)",
    "SCROLL_HANDLE_COLOR": (255, 255, 255, 62),
}

# 侧边栏（mockup .sidebar，历史遗留保留）
SIDEBAR_BG = "#20262e"

# ---- 配色方案（背景色调 × 明暗主题 的二维系统） ----
#
# 每个方案只声明「色相敏感层」的覆盖：背景/卡片/文字/强调/主按钮/徽章/通知条
# 与状态色；黑白透明度类的结构令牌（分隔线、ghost 按钮底、滚动条、发丝描边）
# 全部沿用 neutral 明暗模板，在任何色调下都协调。neutral 的覆盖为空 = 现有
# 暖雾灰 / 暮色灰原样。
#
# 状态色约定与 neutral 一致：整套去饱和（同一色相的深浅两档），ALERT 红
# 是全局唯一的彩色提醒，不随配色变。

_PALETTES = {
    "neutral": {
        "label": "暖雾灰 · 暮色灰",
        "light": {},
        "dark": {},
    },
    "sand": {
        "label": "暖沙 · 陶土",
        "light": {
            "BG": "#f1e9da", "CARD": "#fdf9f1", "ROW_INSET": "#f5efe2",
            "BORDER": "#e7ddc9",
            "TEXT": "#38312a", "TEXT_2": "#7a6d5c", "TEXT_3": "#ab9f8c",
            "ACCENT": "#9c7440", "SWITCH_ON": "#9c7440",
            "SWITCH_ON_DARK": "#c9b697",
            "OK": "#4a3f2e", "OK_TINT": "#f0ead9",
            "RUN": "#8a6f4b", "RUN_TINT": "#f2ebdb",
            "WAIT": "#7a6d5c", "WAIT_TINT": "#f4eee1",
            "ERR": "#45372b", "ERR_TINT": "#f0e9de",
            "PRIMARY_BG": "#9c7440", "PRIMARY_FG": "#ffffff",
            "PRIMARY_HOVER": "#ad8552", "PRIMARY_PRESSED": "#86613a",
            "PRIMARY_DISABLED_BG": "rgba(156, 116, 64, 0.35)",
            "PRIMARY_DISABLED_FG": "rgba(255, 255, 255, 0.75)",
            "BADGE_TOP": "#b08d4f", "BADGE_BOTTOM": "#5e4526",
            "INFOBAR_BG": "#f5efe3",
        },
        "dark": {
            "BG": "#221d15", "CARD": "#332b20", "ROW_INSET": "#2a241b",
            "BORDER": "#473c2c",
            "TEXT": "#ede4d3", "TEXT_2": "#b8ab95", "TEXT_3": "#857968",
            "ACCENT": "#d4b276", "SWITCH_ON": "#d4b276",
            "SWITCH_ON_DARK": "#6b5836",
            "OK": "#d6c9ae", "OK_TINT": "#3a3225",
            "RUN": "#cdbd9d", "RUN_TINT": "#3f3728",
            "WAIT": "#b8ab95", "WAIT_TINT": "#373021",
            "ERR": "#f5efe2", "ERR_TINT": "#41382a",
            "PRIMARY_BG": "#d4b276", "PRIMARY_FG": "#221d15",
            "PRIMARY_HOVER": "#e0c189", "PRIMARY_PRESSED": "#bd9a5e",
            "PRIMARY_DISABLED_BG": "rgba(212, 178, 118, 0.25)",
            "PRIMARY_DISABLED_FG": "rgba(34, 29, 21, 0.55)",
            "BADGE_TOP": "#9a7f4a", "BADGE_BOTTOM": "#4b3a20",
            "INFOBAR_BG": "#3f372a",
        },
    },
    "moss": {
        "label": "青瓷 · 苔绿",
        "light": {
            "BG": "#e6ece3", "CARD": "#f8fbf6", "ROW_INSET": "#edf2ea",
            "BORDER": "#d4dfd0",
            "TEXT": "#2b332b", "TEXT_2": "#64725f", "TEXT_3": "#9aa793",
            "ACCENT": "#3f7d52", "SWITCH_ON": "#3f7d52",
            "SWITCH_ON_DARK": "#a3c2a5",
            "OK": "#38453a", "OK_TINT": "#e9efe5",
            "RUN": "#4e7257", "RUN_TINT": "#eaf0e6",
            "WAIT": "#64725f", "WAIT_TINT": "#eef2ea",
            "ERR": "#2e3d30", "ERR_TINT": "#e9efe4",
            "PRIMARY_BG": "#3f7d52", "PRIMARY_FG": "#ffffff",
            "PRIMARY_HOVER": "#4f9163", "PRIMARY_PRESSED": "#346844",
            "PRIMARY_DISABLED_BG": "rgba(63, 125, 82, 0.35)",
            "PRIMARY_DISABLED_FG": "rgba(255, 255, 255, 0.75)",
            "BADGE_TOP": "#63916d", "BADGE_BOTTOM": "#27402c",
            "INFOBAR_BG": "#ebf1e7",
        },
        "dark": {
            "BG": "#161e17", "CARD": "#202b21", "ROW_INSET": "#1a231b",
            "BORDER": "#324234",
            "TEXT": "#e2ebe1", "TEXT_2": "#a4b3a2", "TEXT_3": "#778678",
            "ACCENT": "#90cf9c", "SWITCH_ON": "#90cf9c",
            "SWITCH_ON_DARK": "#46604a",
            "OK": "#c3d4c1", "OK_TINT": "#2b382c",
            "RUN": "#b4c9b1", "RUN_TINT": "#313f31",
            "WAIT": "#a4b3a2", "WAIT_TINT": "#2c372c",
            "ERR": "#eef4ea", "ERR_TINT": "#364536",
            "PRIMARY_BG": "#90cf9c", "PRIMARY_FG": "#16201a",
            "PRIMARY_HOVER": "#a3dbae", "PRIMARY_PRESSED": "#79b585",
            "PRIMARY_DISABLED_BG": "rgba(144, 207, 156, 0.25)",
            "PRIMARY_DISABLED_FG": "rgba(22, 32, 26, 0.55)",
            "BADGE_TOP": "#548a5d", "BADGE_BOTTOM": "#243826",
            "INFOBAR_BG": "#303f31",
        },
    },
    "mist": {
        "label": "雾蓝 · 藏青",
        "light": {
            "BG": "#e7ecf2", "CARD": "#f9fbfe", "ROW_INSET": "#edf1f7",
            "BORDER": "#d5dee9",
            "TEXT": "#29323d", "TEXT_2": "#64707f", "TEXT_3": "#9aa6b4",
            "ACCENT": "#3e6fa8", "SWITCH_ON": "#3e6fa8",
            "SWITCH_ON_DARK": "#a4bdd6",
            "OK": "#37434f", "OK_TINT": "#e9eef5",
            "RUN": "#4a6a8f", "RUN_TINT": "#e9eef4",
            "WAIT": "#64707f", "WAIT_TINT": "#eef1f6",
            "ERR": "#2c3844", "ERR_TINT": "#e9edf3",
            "PRIMARY_BG": "#3e6fa8", "PRIMARY_FG": "#ffffff",
            "PRIMARY_HOVER": "#4f81ba", "PRIMARY_PRESSED": "#355d8d",
            "PRIMARY_DISABLED_BG": "rgba(62, 111, 168, 0.35)",
            "PRIMARY_DISABLED_FG": "rgba(255, 255, 255, 0.75)",
            "BADGE_TOP": "#5c85b3", "BADGE_BOTTOM": "#263a52",
            "INFOBAR_BG": "#ecf0f6",
        },
        "dark": {
            "BG": "#151a21", "CARD": "#1f2731", "ROW_INSET": "#1a2029",
            "BORDER": "#333e4e",
            "TEXT": "#e3e9f1", "TEXT_2": "#a7b3c3", "TEXT_3": "#77839a",
            "ACCENT": "#8cbde8", "SWITCH_ON": "#8cbde8",
            "SWITCH_ON_DARK": "#43607f",
            "OK": "#c2d0e0", "OK_TINT": "#2a3440",
            "RUN": "#b3c3d5", "RUN_TINT": "#313d4b",
            "WAIT": "#a7b3c3", "WAIT_TINT": "#2c3641",
            "ERR": "#edf2f8", "ERR_TINT": "#35404e",
            "PRIMARY_BG": "#8cbde8", "PRIMARY_FG": "#141a21",
            "PRIMARY_HOVER": "#a1cbec", "PRIMARY_PRESSED": "#76a8d4",
            "PRIMARY_DISABLED_BG": "rgba(140, 189, 232, 0.25)",
            "PRIMARY_DISABLED_FG": "rgba(20, 26, 33, 0.55)",
            "BADGE_TOP": "#56779c", "BADGE_BOTTOM": "#24334a",
            "INFOBAR_BG": "#2d3947",
        },
    },
    "plum": {
        "label": "藕荷 · 绛紫",
        "light": {
            "BG": "#ece8ef", "CARD": "#fbf8fd", "ROW_INSET": "#f1edf4",
            "BORDER": "#ded7e5",
            "TEXT": "#322d3a", "TEXT_2": "#6f6679", "TEXT_3": "#a49aae",
            "ACCENT": "#7a5c9e", "SWITCH_ON": "#7a5c9e",
            "SWITCH_ON_DARK": "#b3a0c8",
            "OK": "#443c4e", "OK_TINT": "#efeaf4",
            "RUN": "#6f5a88", "RUN_TINT": "#eee9f3",
            "WAIT": "#6f6679", "WAIT_TINT": "#f2eef5",
            "ERR": "#3b3344", "ERR_TINT": "#eee9f2",
            "PRIMARY_BG": "#7a5c9e", "PRIMARY_FG": "#ffffff",
            "PRIMARY_HOVER": "#8b6db2", "PRIMARY_PRESSED": "#664d84",
            "PRIMARY_DISABLED_BG": "rgba(122, 92, 158, 0.35)",
            "PRIMARY_DISABLED_FG": "rgba(255, 255, 255, 0.75)",
            "BADGE_TOP": "#9177b0", "BADGE_BOTTOM": "#3d2f52",
            "INFOBAR_BG": "#f0ebf5",
        },
        "dark": {
            "BG": "#1c1822", "CARD": "#282232", "ROW_INSET": "#211c28",
            "BORDER": "#3a3244",
            "TEXT": "#e9e4f0", "TEXT_2": "#afa6bd", "TEXT_3": "#827a90",
            "ACCENT": "#bfa3de", "SWITCH_ON": "#bfa3de",
            "SWITCH_ON_DARK": "#57466d",
            "OK": "#d2c8de", "OK_TINT": "#322b3c",
            "RUN": "#c4b8d3", "RUN_TINT": "#3a3246",
            "WAIT": "#afa6bd", "WAIT_TINT": "#342d3e",
            "ERR": "#f3eef8", "ERR_TINT": "#3e3549",
            "PRIMARY_BG": "#bfa3de", "PRIMARY_FG": "#1c1822",
            "PRIMARY_HOVER": "#ccb4e6", "PRIMARY_PRESSED": "#aa8fc9",
            "PRIMARY_DISABLED_BG": "rgba(191, 163, 222, 0.25)",
            "PRIMARY_DISABLED_FG": "rgba(28, 24, 34, 0.55)",
            "BADGE_TOP": "#776191", "BADGE_BOTTOM": "#322847",
            "INFOBAR_BG": "#393044",
        },
    },
}

# 配色方案的合法名与下拉顺序
PALETTES = tuple(_PALETTES)
DEFAULT_PALETTE = "neutral"

# ---- 语义功能色（全部配色共用，部件级彩色点缀） ----
# 成功=绿 / 运行=蓝 / 等待=琥珀 / 失败=红。这些色最后覆盖进每套调色板：
# 仪表盘状态点、耗时数字、结果圆点、Pill 徽章随之呈现真正的功能色彩，
# 不再整版单色。明亮档深字浅底、暗夜档亮字深底，PILL_FAIL_FG 随之取对比色。
_SEMANTIC = {
    "light": {
        "OK": "#2f7d46", "OK_TINT": "#e6f3ea",
        "RUN": "#2f6cb0", "RUN_TINT": "#e7f0fa",
        "WAIT": "#a8730f", "WAIT_TINT": "#faf0da",
        "ERR": "#b23b32", "ERR_TINT": "#fbeae8",
        "PILL_FAIL_FG": "#ffffff",
    },
    "dark": {
        "OK": "#7fd18f", "OK_TINT": "#24382b",
        "RUN": "#82b8ec", "RUN_TINT": "#23344a",
        "WAIT": "#e3b95f", "WAIT_TINT": "#3d3220",
        "ERR": "#f0968c", "ERR_TINT": "#452b28",
        "PILL_FAIL_FG": "#2a1512",
    },
}

# 一次写入明暗两套变色的接口用（窗口背景 / 通知条底色 / 滚动条滑块）
BG_LIGHT = _LIGHT["BG"]
BG_DARK = _DARK["BG"]
INFOBAR_BG_LIGHT = _LIGHT["INFOBAR_BG"]
INFOBAR_BG_DARK = _DARK["INFOBAR_BG"]
SCROLL_HANDLE_LIGHT = _LIGHT["SCROLL_HANDLE_COLOR"]
SCROLL_HANDLE_DARK = _DARK["SCROLL_HANDLE_COLOR"]

# ---- 即时换肤：样式配方登记 ----

# bind() 登记的 widget 弱引用；apply() 末尾统一重套，销毁的顺带清理
_bound_refs = []
_current = "light"
_current_palette = DEFAULT_PALETTE


def _merged(mode, palette_name):
    """指定明暗 × 配色的完整调色板：neutral 模板 + 方案覆盖。"""
    base = _DARK if mode == "dark" else _LIGHT
    pal = _PALETTES.get(palette_name) or _PALETTES[DEFAULT_PALETTE]
    overrides = pal["dark"] if mode == "dark" else pal["light"]
    merged = {**base, **overrides}
    merged.update(_SEMANTIC[mode])   # 语义功能色最后覆盖（部件级彩色）
    return merged


def apply(name, palette_name=DEFAULT_PALETTE):
    """切换主题并重套全部绑定样式。

    name: "light" / "dark"；palette_name: PALETTES 里的配色方案名
    （未知值回退默认配色）。末尾同步刷新 BG_LIGHT 等「明暗两套一次写入」
    常量——它们在 import 时固定，换配色后必须跟着更新，窗口背景与
    通知条底色才能拿到新色调。
    """
    global _current, _current_palette
    pal = _PALETTES.get(palette_name) or _PALETTES[DEFAULT_PALETTE]
    palette_name = next(k for k, v in _PALETTES.items() if v is pal)
    merged = _merged(name, palette_name)
    for key, value in merged.items():
        globals()[key] = value
    _current = "dark" if name == "dark" else "light"
    _current_palette = palette_name
    light, dark = _merged("light", palette_name), _merged("dark", palette_name)
    globals()["BG_LIGHT"] = light["BG"]
    globals()["BG_DARK"] = dark["BG"]
    globals()["INFOBAR_BG_LIGHT"] = light["INFOBAR_BG"]
    globals()["INFOBAR_BG_DARK"] = dark["INFOBAR_BG"]
    _rebind_all()    # 即时换肤核心：重套所有 bind() 配方（无绑定时空转）


def bind(widget, qss_fn):
    """登记样式配方：立即应用 qss_fn() 生成的样式表，主题切换时自动重套。

    同一 widget 重复绑定时以最后一次为准（style_button 之后再补一条
    覆盖样式的场景）；widget 销毁后条目在下次切换时自动清理。
    """
    widget._theme_qss = qss_fn
    refresh(widget)
    if not any(r() is widget for r in _bound_refs):
        _bound_refs.append(weakref.ref(widget))


def refresh(widget):
    """立即重套单个 widget 的绑定样式（运行中的状态色变化后调用）。"""
    fn = getattr(widget, "_theme_qss", None)
    if fn is None:
        return
    try:
        widget.setStyleSheet(fn())
    except (RuntimeError, AttributeError):
        _forget(widget)          # C++ 对象已销毁：清掉失效绑定


def _forget(widget):
    _bound_refs[:] = [r for r in _bound_refs if r() is not widget]


def _rebind_all():
    """主题切换后重套全部绑定样式；已销毁的条目顺带清理。"""
    alive = []
    for r in _bound_refs:
        w = r()
        if w is None:
            continue
        alive.append(r)
        fn = getattr(w, "_theme_qss", None)
        if fn is None:
            continue
        try:
            w.setStyleSheet(fn())
        except (RuntimeError, AttributeError):
            alive.pop()          # 本次切换中已销毁：丢弃
    _bound_refs[:] = alive


def palette(name):
    """当前配色下指定明暗的调色板 dict（name: "light" / "dark"）。"""
    return _merged(name, _current_palette)


def accent_of(name, palette_name=None):
    """指定主题（可带目标配色）的强调色（切换时先于 apply 交给 setThemeColor 用）。"""
    return _merged(name, palette_name or _current_palette)["ACCENT"]


def is_dark():
    return _current == "dark"


def theme_name():
    return _current


def palette_name():
    """当前配色方案名（PALETTES 之一）。"""
    return _current_palette


def palette_labels():
    """[(方案名, 显示名)] 有序列表（设置页下拉直接用）。"""
    return [(k, _PALETTES[k]["label"]) for k in PALETTES]


def font_stack(size, weight="400"):
    """QSS font 简写：font_stack(13, '600') → 'font-size: 13px; font-weight: 600;'。"""
    return "font-size: %spx; font-weight: %s;" % (size, weight)


# 模块导入时先落一份明亮值，保证 import 期读取默认参数等场景不缺色；
# 窗口构造时会按配置重新 apply。
apply("light")
