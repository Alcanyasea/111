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
# 五套配色全部取自知名风格系统的官方色板（千万人检验过的审美）：
#   github      GitHub Primer —— 清爽极简，开发者最熟悉的灰白蓝绿
#   catppuccin  Catppuccin Latte/Frappé —— 奶油底柔彩，社区宠儿
#   everforest  Everforest Light/Medium —— 柔和森林绿，护眼暖米底
#   nord        Nord Snow Storm/Polar Night —— 冷冽蓝灰，Aurora 四色点缀
#   tokyonight  Tokyo Night Day/Night —— 深夜蓝紫与霓虹语义色
# 每个方案只声明「色相敏感层」的覆盖：背景/卡片/文字/强调/主按钮/徽章/
# 通知条与语义状态色（成功绿/运行蓝/等待黄/失败红 随主题自带的色板走）；
# 黑白透明度类的结构令牌（分隔线、ghost 按钮底、滚动条、发丝描边）沿用
# neutral 明暗模板。ALERT 警示红保持全局唯一「必须人工处理」提醒不随配色变。

_PALETTES = {
    "github": {
        "label": "清爽极简 · GitHub",
        "light": {
            "BG": "#f6f8fa", "CARD": "#ffffff", "ROW_INSET": "#f6f8fa",
            "BORDER": "#d1d9e0",
            "TEXT": "#1f2328", "TEXT_2": "#59636e", "TEXT_3": "#818b98",
            "ACCENT": "#0969da", "SWITCH_ON": "#0969da",
            "SWITCH_ON_DARK": "#a5cdf1",
            "OK": "#1a7f37", "OK_TINT": "#dafbe1",
            "RUN": "#0969da", "RUN_TINT": "#ddf4ff",
            "WAIT": "#9a6700", "WAIT_TINT": "#fff8c5",
            "ERR": "#d1242f", "ERR_TINT": "#ffebe9",
            "PRIMARY_BG": "#1f883d", "PRIMARY_FG": "#ffffff",
            "PRIMARY_HOVER": "#2da44e", "PRIMARY_PRESSED": "#1b6f37",
            "PRIMARY_DISABLED_BG": "rgba(31, 136, 61, 0.35)",
            "PRIMARY_DISABLED_FG": "rgba(255, 255, 255, 0.75)",
            "BADGE_TOP": "#4a7bc4", "BADGE_BOTTOM": "#1c3f77",
            "INFOBAR_BG": "#fbfcfd",
        },
        "dark": {
            "BG": "#0d1117", "CARD": "#161b22", "ROW_INSET": "#10151c",
            "BORDER": "#30363d",
            "TEXT": "#e6edf3", "TEXT_2": "#9198a1", "TEXT_3": "#6e7681",
            "ACCENT": "#2f81f7", "SWITCH_ON": "#2f81f7",
            "SWITCH_ON_DARK": "#1f6feb",
            "OK": "#3fb950", "OK_TINT": "#0e2b1d",
            "RUN": "#4493f8", "RUN_TINT": "#102a42",
            "WAIT": "#d29922", "WAIT_TINT": "#2d2410",
            "ERR": "#f85149", "ERR_TINT": "#3c1614",
            "PRIMARY_BG": "#238636", "PRIMARY_FG": "#ffffff",
            "PRIMARY_HOVER": "#2ea043", "PRIMARY_PRESSED": "#1f6b2e",
            "PRIMARY_DISABLED_BG": "rgba(35, 134, 54, 0.35)",
            "PRIMARY_DISABLED_FG": "rgba(255, 255, 255, 0.75)",
            "BADGE_TOP": "#388bfd", "BADGE_BOTTOM": "#1158c7",
            "INFOBAR_BG": "#1c2431",
        },
    },
    "catppuccin": {
        "label": "奶油柔彩 · Catppuccin",
        "light": {
            "BG": "#e6e9ef", "CARD": "#eff1f5", "ROW_INSET": "#dce0e8",
            "BORDER": "#ccd0da",
            "TEXT": "#4c4f69", "TEXT_2": "#6c6f85", "TEXT_3": "#9ca0b0",
            "ACCENT": "#1e66f5", "SWITCH_ON": "#1e66f5",
            "SWITCH_ON_DARK": "#7287fd",
            "OK": "#40a02b", "OK_TINT": "#e6f0e2",
            "RUN": "#1e66f5", "RUN_TINT": "#e3e9fb",
            "WAIT": "#df8e1d", "WAIT_TINT": "#f5ecd9",
            "ERR": "#d20f39", "ERR_TINT": "#f6dfe3",
            "PRIMARY_BG": "#1e66f5", "PRIMARY_FG": "#ffffff",
            "PRIMARY_HOVER": "#4a7df7", "PRIMARY_PRESSED": "#154cc4",
            "PRIMARY_DISABLED_BG": "rgba(30, 102, 245, 0.35)",
            "PRIMARY_DISABLED_FG": "rgba(255, 255, 255, 0.75)",
            "BADGE_TOP": "#7287fd", "BADGE_BOTTOM": "#3a49c0",
            "INFOBAR_BG": "#e9ecf2",
        },
        "dark": {
            "BG": "#292c3c", "CARD": "#303446", "ROW_INSET": "#232634",
            "BORDER": "#414559",
            "TEXT": "#c6d0f5", "TEXT_2": "#a5adce", "TEXT_3": "#737994",
            "ACCENT": "#8caaee", "SWITCH_ON": "#8caaee",
            "SWITCH_ON_DARK": "#454c75",
            "OK": "#a6d189", "OK_TINT": "#33402c",
            "RUN": "#8caaee", "RUN_TINT": "#2f3a55",
            "WAIT": "#e5c890", "WAIT_TINT": "#413b27",
            "ERR": "#e78284", "ERR_TINT": "#472d33",
            "PRIMARY_BG": "#8caaee", "PRIMARY_FG": "#232634",
            "PRIMARY_HOVER": "#9fb5f1", "PRIMARY_PRESSED": "#7899e4",
            "PRIMARY_DISABLED_BG": "rgba(140, 170, 238, 0.25)",
            "PRIMARY_DISABLED_FG": "rgba(35, 38, 52, 0.55)",
            "BADGE_TOP": "#7986e3", "BADGE_BOTTOM": "#3b4585",
            "INFOBAR_BG": "#414559",
        },
    },
    "everforest": {
        "label": "森林绿意 · Everforest",
        "light": {
            "BG": "#efebd4", "CARD": "#fdf6e3", "ROW_INSET": "#f4f0d9",
            "BORDER": "#e4ddc4",
            "TEXT": "#5c6a72", "TEXT_2": "#7a8478", "TEXT_3": "#a6b0a0",
            "ACCENT": "#8da101", "SWITCH_ON": "#8da101",
            "SWITCH_ON_DARK": "#d5cd8a",
            "OK": "#8da101", "OK_TINT": "#ecf0d3",
            "RUN": "#35a77c", "RUN_TINT": "#ddf0e9",
            "WAIT": "#dfa000", "WAIT_TINT": "#f7eed7",
            "ERR": "#f85552", "ERR_TINT": "#fbe3dc",
            "PRIMARY_BG": "#8da101", "PRIMARY_FG": "#fdf6e3",
            "PRIMARY_HOVER": "#9cae1b", "PRIMARY_PRESSED": "#7a8c0c",
            "PRIMARY_DISABLED_BG": "rgba(141, 161, 1, 0.35)",
            "PRIMARY_DISABLED_FG": "rgba(253, 246, 227, 0.75)",
            "BADGE_TOP": "#a7b04a", "BADGE_BOTTOM": "#6b7327",
            "INFOBAR_BG": "#f4f0d9",
        },
        "dark": {
            "BG": "#2d353b", "CARD": "#343f44", "ROW_INSET": "#313c42",
            "BORDER": "#475258",
            "TEXT": "#d3c6aa", "TEXT_2": "#9da9a0", "TEXT_3": "#7a8478",
            "ACCENT": "#a7c080", "SWITCH_ON": "#a7c080",
            "SWITCH_ON_DARK": "#4b5a32",
            "OK": "#a7c080", "OK_TINT": "#333d2c",
            "RUN": "#83c092", "RUN_TINT": "#2b3d39",
            "WAIT": "#dbbc7f", "WAIT_TINT": "#3f3926",
            "ERR": "#e67e80", "ERR_TINT": "#46302e",
            "PRIMARY_BG": "#a7c080", "PRIMARY_FG": "#2d353b",
            "PRIMARY_HOVER": "#b3cc8e", "PRIMARY_PRESSED": "#93ad6f",
            "PRIMARY_DISABLED_BG": "rgba(167, 192, 128, 0.25)",
            "PRIMARY_DISABLED_FG": "rgba(45, 53, 59, 0.55)",
            "BADGE_TOP": "#7f9a55", "BADGE_BOTTOM": "#45532c",
            "INFOBAR_BG": "#3d484d",
        },
    },
    "nord": {
        "label": "冷冽蓝灰 · Nord",
        "light": {
            "BG": "#e5e9f0", "CARD": "#eceff4", "ROW_INSET": "#dde3ed",
            "BORDER": "#d8dee9",
            "TEXT": "#2e3440", "TEXT_2": "#4c566a", "TEXT_3": "#8f9cb3",
            "ACCENT": "#5e81ac", "SWITCH_ON": "#5e81ac",
            "SWITCH_ON_DARK": "#b6c6dd",
            "OK": "#4f7a44", "OK_TINT": "#e7efe3",
            "RUN": "#5e81ac", "RUN_TINT": "#e4ebf3",
            "WAIT": "#9d7c17", "WAIT_TINT": "#f2ecd9",
            "ERR": "#ad4a54", "ERR_TINT": "#f3e2e3",
            "PRIMARY_BG": "#5e81ac", "PRIMARY_FG": "#ffffff",
            "PRIMARY_HOVER": "#6f91b8", "PRIMARY_PRESSED": "#4f7097",
            "PRIMARY_DISABLED_BG": "rgba(94, 129, 172, 0.35)",
            "PRIMARY_DISABLED_FG": "rgba(255, 255, 255, 0.75)",
            "BADGE_TOP": "#7b93ba", "BADGE_BOTTOM": "#3a4d68",
            "INFOBAR_BG": "#e9edf4",
        },
        "dark": {
            "BG": "#2e3440", "CARD": "#3b4252", "ROW_INSET": "#333a47",
            "BORDER": "#434c5e",
            "TEXT": "#eceff4", "TEXT_2": "#d8dee9", "TEXT_3": "#7b88a1",
            "ACCENT": "#88c0d0", "SWITCH_ON": "#88c0d0",
            "SWITCH_ON_DARK": "#3f5b66",
            "OK": "#a3be8c", "OK_TINT": "#333b30",
            "RUN": "#81a1c1", "RUN_TINT": "#2c3a49",
            "WAIT": "#ebcb8b", "WAIT_TINT": "#403b28",
            "ERR": "#bf616a", "ERR_TINT": "#43303a",
            "PRIMARY_BG": "#88c0d0", "PRIMARY_FG": "#2e3440",
            "PRIMARY_HOVER": "#94cbdc", "PRIMARY_PRESSED": "#76aec0",
            "PRIMARY_DISABLED_BG": "rgba(136, 192, 208, 0.25)",
            "PRIMARY_DISABLED_FG": "rgba(46, 52, 64, 0.55)",
            "BADGE_TOP": "#6d93b8", "BADGE_BOTTOM": "#3b5269",
            "INFOBAR_BG": "#404a5c",
        },
    },
    "tokyonight": {
        "label": "东京夜话 · Tokyo Night",
        "light": {
            "BG": "#e1e2e7", "CARD": "#e9eaf2", "ROW_INSET": "#d8dce6",
            "BORDER": "#c4c8da",
            "TEXT": "#3760bf", "TEXT_2": "#6172b0", "TEXT_3": "#a8aecb",
            "ACCENT": "#2e7de9", "SWITCH_ON": "#2e7de9",
            "SWITCH_ON_DARK": "#b3c4f2",
            "OK": "#387068", "OK_TINT": "#dceae6",
            "RUN": "#2e7de9", "RUN_TINT": "#dde8fa",
            "WAIT": "#8c6c3e", "WAIT_TINT": "#ece5d3",
            "ERR": "#c43e5c", "ERR_TINT": "#f5dee4",
            "PRIMARY_BG": "#2e7de9", "PRIMARY_FG": "#ffffff",
            "PRIMARY_HOVER": "#3a86f0", "PRIMARY_PRESSED": "#2568c4",
            "PRIMARY_DISABLED_BG": "rgba(46, 125, 233, 0.35)",
            "PRIMARY_DISABLED_FG": "rgba(255, 255, 255, 0.75)",
            "BADGE_TOP": "#5b7fd4", "BADGE_BOTTOM": "#2c4a8f",
            "INFOBAR_BG": "#e6e8ef",
        },
        "dark": {
            "BG": "#1a1b26", "CARD": "#24283b", "ROW_INSET": "#1f2335",
            "BORDER": "#2f334d",
            "TEXT": "#c0caf5", "TEXT_2": "#a9b1d6", "TEXT_3": "#565f89",
            "ACCENT": "#7aa2f7", "SWITCH_ON": "#7aa2f7",
            "SWITCH_ON_DARK": "#292e42",
            "OK": "#9ece6a", "OK_TINT": "#273427",
            "RUN": "#7aa2f7", "RUN_TINT": "#232c47",
            "WAIT": "#e0af68", "WAIT_TINT": "#3a3423",
            "ERR": "#f7768e", "ERR_TINT": "#43273a",
            "PRIMARY_BG": "#7aa2f7", "PRIMARY_FG": "#1a1b26",
            "PRIMARY_HOVER": "#89b4fa", "PRIMARY_PRESSED": "#6b90dd",
            "PRIMARY_DISABLED_BG": "rgba(122, 162, 247, 0.25)",
            "PRIMARY_DISABLED_FG": "rgba(26, 27, 38, 0.55)",
            "BADGE_TOP": "#5d6fb0", "BADGE_BOTTOM": "#2d3354",
            "INFOBAR_BG": "#292e42",
        },
    },
}

# 配色方案的合法名与下拉顺序
PALETTES = tuple(_PALETTES)
DEFAULT_PALETTE = "github"

# 一次写入明暗两套变色的接口用（窗口背景 / 通知条底色 / 滚动条滑块）：
# BG_LIGHT 等四个在 apply() 里随配色方案刷新；滚动条滑块是黑白透明度、
# 各配色通用，保持常量即可。
SCROLL_HANDLE_LIGHT = (0, 0, 0, 46)
SCROLL_HANDLE_DARK = (255, 255, 255, 62)

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
