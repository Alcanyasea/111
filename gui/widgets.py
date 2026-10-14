# -*- coding: utf-8 -*-
"""通用小组件：状态徽章 Pill、键值行 KV、带标题卡片 Card、账号序号圆标 IconBadge。

macOS 风格约定（配色不变，只管形状与排版）：
- 卡片 12px 圆角 + 发丝描边（Apple 卡片那种若隐若现的轮廓）
- 按钮 8px 圆角、ghost 描边式；破坏性操作用黑字细描边，不用彩色
- 徽章 Pill 全圆角、中等字重
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QGraphicsDropShadowEffect,
                               QHBoxLayout, QLabel, QVBoxLayout, QWidget,
                               QSizePolicy)

from qfluentwidgets import BodyLabel, CardWidget, IconWidget, SubtitleLabel

import theme


def _rgba(hex_color, alpha):
    """'#rrggbb' → 'rgba(r, g, b, a)'（a 取 0~255）。"""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "rgba(%d, %d, %d, %s)" % (r, g, b, alpha)


class Pill(QLabel):
    """全圆角状态徽章：ok / run / fail / wait / alert 五种配色，可带圆点。

    常规态 = 主题淡色底 + 状态色文字 + 同色发丝描边（iOS 16 徽章质感）；
    alert（token 失效）保持全彩实底 —— 全套唯一的高饱和提醒，一眼可辨。

    配色配方 bind 到主题：set_state 换状态、切换主题时都会重套，
    不再依赖「重建窗口后自动生效」。
    """

    def __init__(self, text="", parent=None, dot=False):
        super().__init__(parent)
        self._kind = "wait"
        self._dot = dot
        self._text = text
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        theme.bind(self, self._style_qss)
        self.set_state("wait", text)

    @staticmethod
    def _styles():
        return {
            "ok": (theme.OK, theme.OK_TINT),
            "run": (theme.RUN, theme.RUN_TINT),
            "fail": (theme.ERR, theme.ERR_TINT),
            "wait": (theme.WAIT, theme.WAIT_TINT),
            # token 失效提醒：唯一的彩色徽章（用户要求「标红」提醒）
            "alert": (theme.PILL_ALERT_FG, theme.ALERT),
        }

    def _style_qss(self):
        fg, bg = self._styles().get(self._kind, self._styles()["wait"])
        pad = "3px 10px" if self._dot else "3px 11px"
        return (
            "QLabel { background: %s; color: %s; border: none;"
            " border-radius: 99px; padding: %s; %s font-family: %s; }"
            % (bg, fg, pad, theme.font_stack(theme.FS_MICRO, "600"),
               theme.FONT_FAMILY)
        )

    def _plain_fg(self):
        """当前状态的文字色（圆点颜色用；alert 态下圆点同前景）。"""
        return self._styles().get(self._kind, self._styles()["wait"])[0]

    def _compose_text(self):
        if self._dot:
            self.setText('<span style="color:%s">&#9679;</span>&nbsp;%s'
                         % (self._plain_fg(), self._text))
        else:
            self.setText(self._text)

    def set_state(self, kind, text, dot=None):
        self._kind = kind
        if text is not None:
            self._text = text
        if dot is not None:
            self._dot = bool(dot)
        theme.refresh(self)   # 配色随新状态与新主题立即重套
        self._compose_text()


def dark_log_qss(selector="QPlainTextEdit"):
    """深色日志框统一样式：账号捕获 / MAA 更新 / 导出等实时输出框共用。"""
    return (
        "%s { background: %s; color: %s; font-family: %s;"
        " font-size: %spx; border: none; border-radius: %dpx;"
        " padding: 12px 14px; }"
        % (selector, theme.LOG_BG, theme.LOG_FG, theme.FONT_MONO,
           theme.FS_CAPTION, theme.RADIUS_CARD)
    )


def kv_row(key_text, value_widget, value_min_width=0, refs=False):
    """一行「键 … 值」，值右对齐。value_widget 可为 QWidget 或纯文本 str。

    refs=True 时返回 (row, key_label, value_label)，运行时要改文案的场景用；
    注意 key_text 不要传 QLabel：qfluentwidgets 会把非 str 参数当 parent 重载，
    键名会凭空消失。
    """
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    key = BodyLabel(key_text)
    theme.bind(key, lambda: "color: %s; %s background: transparent;"
               % (theme.TEXT_2, theme.font_stack(theme.FS_CAPTION)))
    lay.addWidget(key)
    lay.addStretch(1)
    if isinstance(value_widget, str):
        value_widget = BodyLabel(value_widget)
        value_widget.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lay.addWidget(value_widget, 0, Qt.AlignmentFlag.AlignRight)
    if value_min_width:
        value_widget.setMinimumWidth(value_min_width)
    return (row, key, value_widget) if refs else row


def big_number(num_text, unit_text):
    """「22 分钟」样式的数字 + 单位。数字用等宽字体：数据一列看下来对齐。"""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    num = QLabel(num_text)
    theme.bind(num, lambda: "font-family: %s; %s color: %s;"
               % (theme.FONT_MONO, theme.font_stack(theme.FS_STAT, "600"),
                  theme.TEXT))
    unit = QLabel(unit_text)
    theme.bind(unit, lambda: "font-family: %s; %s color: %s;"
               % (theme.FONT_FAMILY, theme.font_stack(theme.FS_CAPTION),
                  theme.TEXT_2))
    unit.setAlignment(Qt.AlignmentFlag.AlignBottom)
    lay.addWidget(num)
    lay.addWidget(unit, 0, Qt.AlignmentFlag.AlignBottom)
    return w


class Card(CardWidget):
    """主题色卡片：可选标题 + 提示文字，内容用 add_widget 逐行加入。

    背景取 theme.CARD，12px 圆角 + 发丝描边（macOS 卡片质感），
    描边颜色每次绘制时读取，双主题下都保持若隐若现的轮廓。
    """

    def _normalBackgroundColor(self):
        return QColor(theme.CARD)

    def _hoverBackgroundColor(self):
        return QColor(theme.CARD)

    def _pressedBackgroundColor(self):
        return QColor(theme.CARD)

    def __init__(self, title=None, hint=None, parent=None):
        super().__init__(parent)
        self.setBorderRadius(theme.RADIUS_CARD)
        self.apply_shadow()
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(theme.CARD_PAD, theme.CARD_PAD - 2,
                                     theme.CARD_PAD, theme.CARD_PAD)
        self.vbox.setSpacing(0)
        self.title_label = None
        self.hint_label = None
        if title:
            head = QHBoxLayout()
            head.setSpacing(8)
            # 标题左侧 3×14px 强调色竖条：全套卡片共用的识别元素，
            # 颜色实时读取主题（换配色后 bind 重套 + 主窗口全量重画）
            mark = QWidget()
            mark.setFixedSize(3, 14)
            theme.bind(mark, lambda: "background: %s; border-radius: 1px;"
                       % theme.ACCENT)
            head.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
            t = SubtitleLabel(title)
            theme.bind(t, lambda: "font-family: %s; %s color: %s; background: transparent;"
                       % (theme.FONT_FAMILY, theme.font_stack(theme.FS_SUBTITLE, "600"),
                          theme.TEXT))
            self.title_label = t
            head.addWidget(t)
            if hint:
                h = BodyLabel(hint)
                theme.bind(h, lambda: "font-family: %s; %s color: %s;"
                           % (theme.FONT_FAMILY, theme.font_stack(theme.FS_CAPTION),
                              theme.TEXT_3))
                self.hint_label = h
                head.addWidget(h, 0, Qt.AlignmentFlag.AlignBottom)
                head.addStretch(1)
            self.vbox.addLayout(head)
            self.vbox.addSpacing(14)

    def add_widget(self, w, spacing=8):
        self.vbox.addWidget(w)
        self.vbox.addSpacing(spacing)

    def apply_shadow(self):
        """iOS 式柔和投影：大模糊、低不透明度、向下偏移，让卡片从灰底上浮起。

        投影色从 theme.card_shadow_color() 读（明亮 = 背景同色相加深，
        暗夜 = 纯黑），换配色/主题后由 refresh_card_shadows() 整体重建。
        setGraphicsEffect 会替换（并删除）旧特效，账号拖动排序借用了
        透明度特效，落位后需要重新调用本方法恢复投影。
        """
        eff = QGraphicsDropShadowEffect(self)
        eff.setBlurRadius(30)
        eff.setOffset(0, 9)
        eff.setColor(QColor(*theme.card_shadow_color()))
        self.setGraphicsEffect(eff)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(*theme.HAIRLINE), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1),
                          theme.RADIUS_CARD, theme.RADIUS_CARD)


class IconBadge(QLabel):
    """账号卡片左上角的序号圆标：强调色淡底 + 同色数字（iOS 风 tinted 圆标）。

    只承担「运行顺序」一个信息，视觉分量必须低于账号名——38px 渐变块的
    旧版是整卡最重的元素，喧宾夺主；现在 28px 平面淡底圆标，安静但可辨。
    颜色取系统唯一主色 ACCENT（与主按钮/开关/焦点环同色相，不引入新颜色），
    两位数时自动变胶囊（min-width + 左右 padding），全圆角与 Pill 同语言。
    """

    def __init__(self, char, parent=None):
        super().__init__(char, parent)
        self.setMinimumSize(28, 28)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.bind(
            self,
            lambda: "QLabel { background: %s; color: %s;"
                    " font-family: %s; %s border-radius: 14px;"
                    " padding: 0 7px; }"
                    % (_rgba(theme.ACCENT, 0.13), theme.ACCENT,
                       theme.FONT_FAMILY, theme.font_stack(14, "600")))


def refresh_card_shadows():
    """主题/配色切换后重建全部 Card 的投影（投影色随背景色相派生）。

    QGraphicsDropShadowEffect 的颜色不由 QSS 配方驱动，只能整体替换特效；
    在主窗口 _switch_theme 的全量刷新里调用一次即可。
    """
    for w in QApplication.allWidgets():
        if isinstance(w, Card):
            w.apply_shadow()


def apply_mono_font(widget, size=None):
    """数据控件（时间输入框、耗时数字等）改用等宽字体：数字按列对齐。

    直接 QFont.setFamilies，不碰样式表——实例 setStyleSheet 会整体替换
    qfluentwidgets 控件自带 QSS，等宽诉求犯不上付这个代价。
    """
    f = widget.font()
    f.setFamilies(list(theme.FONT_MONO_FAMILIES))
    if size is not None:
        f.setPointSize(int(size))
    widget.setFont(f)


def style_scroll_area(scroll):
    """滚动区统一样式：透明底 + 浅色细滚动条（配方绑定，随主题即时重套）。

    两层处理：
    1. qfluentwidgets ScrollArea 的滚动条是自绘的 SmoothScrollBar
       （3px 常显悬浮条，不吃 QSS），默认黑色 45% 透明度，正好压在
       卡片右缘上像一条黑边 —— 这里把滑块改成低对比浅灰；明暗两套
       变色一次写全，切换主题无需重设；
    2. 经典 QScrollBar 的 QSS 绑定为主题配方（弹窗里的普通滚动区会用到）。
    页面内容右侧再让出 12px（各页 root margins），滑块落在留白里。
    """
    delegate = getattr(scroll, "scrollDelagate", None)
    for name in ("vScrollBar", "hScrollBar"):
        bar = getattr(delegate, name, None) if delegate is not None else None
        handle = getattr(bar, "handle", None) if bar is not None else None
        if handle is not None:
            handle.setLightColor(QColor(*theme.SCROLL_HANDLE_LIGHT))
            handle.setDarkColor(QColor(*theme.SCROLL_HANDLE_DARK))
    theme.bind(
        scroll,
        lambda: "QScrollArea { border: none; background: transparent; }"
                "QScrollArea > QWidget > QWidget { background: transparent; }"
                "QScrollBar:vertical { background: transparent; width: 8px;"
                " margin: 4px 2px 4px 0; }"
                "QScrollBar::handle:vertical { background: %s;"
                " border-radius: 3px; min-height: 40px; }"
                "QScrollBar::handle:vertical:hover { background: %s; }"
                "QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical"
                " { height: 0; width: 0; }"
                "QScrollBar::sub-page:vertical, QScrollBar::add-page:vertical"
                " { background: transparent; }"
                % (theme.SCROLLBAR_HANDLE, theme.SCROLLBAR_HANDLE_HOVER))
    scroll.viewport().setStyleSheet("background: transparent;")
    return scroll


def hline():
    """1px 发丝分隔线（弹窗、卡片内分区用）。"""
    line = QWidget()
    line.setFixedHeight(1)
    theme.bind(line, lambda: "background: %s;" % theme.SEP)
    return line


def inset_row():
    """iOS 设置列表式内嵌行容器：深半档底色 + 6px 圆角。"""
    w = QWidget()
    theme.bind(w, lambda: "QWidget { background: %s; border-radius: %dpx; }"
               % (theme.ROW_INSET, theme.RADIUS_ROW))
    return w


def style_button(btn, kind="ghost", small=False):
    """统一按钮质感：8px 圆角、半透明底描边式（macOS push button 变体）。

    kind: ghost（常规）/ danger（破坏性：淡红底 + 红字，比红描边柔和，
    悬停加深 —— 删除/清空这类操作一眼可辨又不刺眼）
    small: 日志工具栏等次级操作用小一号规格。
    颜色全部取自 theme 令牌并以配方绑定，明亮/暗夜切换即时重套。
    """

    def _qss():
        if kind == "danger":
            # 破坏性操作：淡红填充、无描边，悬停加深一档（比红描边柔和）
            hover = _rgba(theme.ERR, 0.18) if theme.is_dark() \
                else _rgba(theme.ERR, 0.12)
            return (
                "PushButton { background: %s; color: %s;"
                " border: none; border-radius: %dpx; padding: %s;"
                " font-family: %s; %s }"
                "PushButton:hover { background: %s; }"
                "PushButton:pressed { background: %s; }"
                "PushButton:disabled { color: %s; background: %s; }"
                % (theme.ERR_TINT, theme.ERR,
                   theme.RADIUS_BTN, "4px 12px" if small else "6px 16px",
                   theme.FONT_FAMILY,
                   theme.font_stack(theme.FS_CAPTION if small else theme.FS_BODY,
                                    "500"),
                   hover, _rgba(theme.ERR, 0.24),
                   theme.BTN_DISABLED_FG, theme.BTN_DISABLED_BG)
            )
        # 常规按钮：描边式 ghost，悬停提亮底色 + 描边加深一档
        pad = "4px 14px" if small else "6px 16px"
        return (
            "PushButton { background: %s; color: %s;"
            " border: 1px solid %s; border-radius: %dpx; padding: %s;"
            " font-family: %s; %s }"
            "PushButton:hover { background: %s; border-color: %s; }"
            "PushButton:pressed { background: %s; }"
            "PushButton:disabled { color: %s;"
            " border-color: %s; background: %s; }"
            % (theme.BTN_BG, theme.BTN_FG, theme.BTN_BORDER, theme.RADIUS_BTN, pad,
               theme.FONT_FAMILY,
               theme.font_stack(theme.FS_CAPTION if small else theme.FS_BODY,
                                "500"),
               theme.BTN_BG_HOVER, theme.BTN_BORDER_HOVER,
               theme.BTN_BG_PRESSED,
               theme.BTN_DISABLED_FG, theme.BTN_DISABLED_BORDER, theme.BTN_DISABLED_BG)
        )

    theme.bind(btn, _qss)
    btn.setMinimumHeight(theme.BTN_H_SM if small else theme.BTN_H)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def style_primary_button(btn):
    """主按钮：主题强调色实底（macOS Accent button 的柔和灰阶版）。

    注意：实例 setStyleSheet 会整体替换 qfluentwidgets 的按钮 qss，
    所以这里必须写全背景/悬停/按下/禁用各态，不能只写增量。
    """
    theme.bind(
        btn,
        lambda: "PrimaryPushButton { background: %s; color: %s;"
                " border: none; border-radius: %dpx; padding: 6px 20px;"
                " font-family: %s; %s }"
                "PrimaryPushButton:hover { background: %s; }"
                "PrimaryPushButton:pressed { background: %s; }"
                "PrimaryPushButton:disabled { background: %s; color: %s; }"
                % (theme.PRIMARY_BG, theme.PRIMARY_FG, theme.RADIUS_BTN,
                   theme.FONT_FAMILY, theme.font_stack(theme.FS_BODY, "600"),
                   theme.PRIMARY_HOVER, theme.PRIMARY_PRESSED,
                   theme.PRIMARY_DISABLED_BG, theme.PRIMARY_DISABLED_FG))
    btn.setMinimumHeight(theme.BTN_H)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def style_tinted_button(btn, small=False):
    """轻强调按钮：主题强调色低透明度实底（macOS tinted push button 变体）。

    介于 ghost（描边式）与 primary（实底深灰）之间：给卡片内唯一的主操作
    用——比 ghost 有分量，又不会像 primary 那样在多张卡片上重复出现显得
    沉重。色相取当前主题 ACCENT 调透明度（不引入新颜色，保持整套灰阶），
    ACCENT 是 "#rrggbb" 字面量，直接拆出三通道。配方绑定随主题即时重套。
    """

    def _qss():
        rgb = tuple(int(theme.ACCENT[i:i + 2], 16) for i in (1, 3, 5))
        pad = "4px 14px" if small else "6px 18px"
        return (
            "PushButton { background: rgba(%d, %d, %d, 0.14); color: %s;"
            " border: none; border-radius: %dpx; padding: %s;"
            " font-family: %s; %s }"
            "PushButton:hover { background: rgba(%d, %d, %d, 0.21); }"
            "PushButton:pressed { background: rgba(%d, %d, %d, 0.28); }"
            "PushButton:disabled { background: rgba(%d, %d, %d, 0.07);"
            " color: %s; }"
            % (rgb + (theme.TEXT, theme.RADIUS_BTN, pad, theme.FONT_FAMILY,
                      theme.font_stack(
                          theme.FS_CAPTION if small else theme.FS_BODY, "600"))
               + rgb + rgb + rgb + (theme.TEXT_3,))
        )

    theme.bind(btn, _qss)
    btn.setMinimumHeight(theme.BTN_H_SM if small else theme.BTN_H)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def empty_state(title, hint=None, icon=None):
    """居中空态视图：大图标（可选）+ 标题 + 辅注。

    空列表/空详情不该只是一行干巴巴的灰字——居中构图给出「这里是空的、
    下一步干什么」的引导。上下留白用固定间距（stretch 不计入 sizeHint，
    账号页网格是按 sizeHint 绝对定位的），底部略大于顶部做光学居中。
    返回普通 QWidget，sizeHint 可直接参与布局。
    """
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(theme.SPACE_2)
    v.addSpacing(28)
    if icon is not None:
        ic = IconWidget(icon)
        ic.setFixedSize(44, 44)
        ic.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        theme.bind(ic, lambda: "background: transparent;")
        v.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(theme.SPACE_2)
    t = QLabel(title)
    t.setAlignment(Qt.AlignmentFlag.AlignCenter)
    theme.bind(t, lambda: "font-family: %s; %s color: %s;"
               " background: transparent;"
               % (theme.FONT_FAMILY, theme.font_stack(theme.FS_BODY, "600"),
                  theme.TEXT_2))
    v.addWidget(t)
    if hint:
        h = QLabel(hint)
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.bind(h, lambda: "font-family: %s; %s color: %s;"
                   " background: transparent;"
                   % (theme.FONT_FAMILY, theme.font_stack(theme.FS_CAPTION),
                      theme.TEXT_3))
        v.addWidget(h)
    v.addSpacing(40)
    return w


def _label_transparent(widget):
    """让 Fluent 文本标签透明，避免在灰卡片上画出底色方块。

    qfluentwidgets 的 FluentLabelBase 样式本身不设背景，但在带背景色
    样式表的父级里会把调色板 Window 色画出来，补一条 background 规则覆盖。
    """
    old = widget.styleSheet() or ""
    if "background: transparent" not in old:
        widget.setStyleSheet(old + "\nFluentLabelBase { background: transparent; }")


def set_switch_checked_gray(switch, text=None):
    """开关开启态从主题青色改为深灰，与灰底卡片风格统一。

    text 不为空时同步 on/off 文案：qfluentwidgets 的 SwitchButton 在切换
    状态时会用 onText/offText（默认 On/Off）覆盖 setText 的内容，只调
    setText 的话一开一关文字就会变回 On/Off。
    """
    switch.setCheckedIndicatorColor(theme.SWITCH_ON, theme.SWITCH_ON_DARK)
    if text is not None:
        switch.setOnText(text)
        switch.setOffText(text)
        switch.setText(text)
    return switch


class BusyStrip(QWidget):
    """不确定进度光带（3px）：运行中的页头/账号卡片底部用，让「正在跑」可见。

    主题色在 paintEvent 里实时读取（主题切换重建窗口后天然生效）；
    start/stop 控制定时器，隐藏时不占用布局空间。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._pos = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def _tick(self):
        self._pos = (self._pos + 0.012) % 1.0   # 30fps 下约 2.8 秒扫一趟
        self.update()

    def start(self):
        if not self._timer.isActive():
            self._timer.start(33)
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        p.setPen(Qt.PenStyle.NoPen)
        hair = theme.HAIRLINE
        p.setBrush(QColor(hair[0], hair[1], hair[2], 36))
        p.drawRoundedRect(0, 0, w, 3, 1.5, 1.5)
        bar_w = max(28, int(w * 0.2))
        travel = w + 2 * bar_w
        x = -bar_w + self._pos * travel
        grad = QLinearGradient(x, 0, x + bar_w, 0)
        c0 = QColor(theme.ACCENT)
        c0.setAlpha(30)
        c1 = QColor(theme.ACCENT)
        c1.setAlpha(210)
        grad.setColorAt(0.0, c0)
        grad.setColorAt(0.5, c1)
        grad.setColorAt(1.0, c0)
        p.setBrush(grad)
        p.drawRoundedRect(int(x), 0, bar_w, 3, 1.5, 1.5)
