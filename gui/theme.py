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


def apply(name):
    """把指定主题的调色板写入模块级变量，并重套全部绑定样式。name: "light" / "dark"。"""
    global _current
    palette_ = _DARK if name == "dark" else _LIGHT
    for key, value in palette_.items():
        globals()[key] = value
    _current = "dark" if name == "dark" else "light"
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
    """指定主题的调色板 dict（供一次写入明暗两套变色的接口取值）。"""
    return _DARK if name == "dark" else _LIGHT


def accent_of(name):
    """指定主题的强调色（切换时先于 apply 交给 setThemeColor 用）。"""
    return palette(name)["ACCENT"]


def is_dark():
    return _current == "dark"


def theme_name():
    return _current


def font_stack(size, weight="400"):
    """QSS font 简写：font_stack(13, '600') → 'font-size: 13px; font-weight: 600;'。"""
    return "font-size: %spx; font-weight: %s;" % (size, weight)


# 模块导入时先落一份明亮值，保证 import 期读取默认参数等场景不缺色；
# 窗口构造时会按配置重新 apply。
apply("light")
