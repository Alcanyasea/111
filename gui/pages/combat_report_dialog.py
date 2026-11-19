"""Console-owned combat reports. Never reads a MAA installation or its history."""
from pathlib import Path
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QScrollArea,
                               QHeaderView, QTableWidget, QTableWidgetItem)
from qfluentwidgets import PushButton
import theme
from sheets import SheetDialog
from widgets import label, style_button, style_scroll_area

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plugins.combat_report.combat_report import load_reports, summarize, game_day


class CombatReportDialog(SheetDialog):
    def __init__(self, cfg, history_dir, parent=None):
        super().__init__(parent, title='每账号作战日报', size=(950, 544))
        self.history_dir = history_dir
        self.names = {str(a.get('id')): a.get('label') or a.get('name') or a.get('id') for a in cfg.get('accounts', [])}
        root = self.body
        bar = QHBoxLayout()
        self.account_filter = QComboBox()
        self.day_filter = QComboBox()
        for caption, combo in (('账号', self.account_filter), ('游戏日', self.day_filter)):
            combo.setAccessibleName(caption)
            bar.addWidget(label(caption, 13, '600'))
            theme.bind(combo, lambda: 'QComboBox { background:%s; color:%s; border:1px solid %s;'
                       ' border-radius:8px; padding:5px 10px; }'
                       'QAbstractItemView { background:%s; color:%s; selection-background-color:%s; }'
                       % (theme.ROW_INSET, theme.TEXT, theme.BORDER, theme.CARD, theme.TEXT,
                          theme.PRIMARY_BG))
            bar.addWidget(combo, 1)
            bar.addSpacing(8)
        self.reload_button = style_button(PushButton('刷新日报'), small=True)
        self.reload_button.clicked.connect(self.reload)
        bar.addWidget(self.reload_button)
        root.addLayout(bar)
        self.summary = label('', 14, '600', transparent=True)
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setWordWrap(True)
        self.summary.setMargin(8)
        summary_scroll = style_scroll_area(QScrollArea())
        summary_scroll.setAccessibleName('所选范围汇总')
        summary_scroll.setWidgetResizable(True)
        summary_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        summary_scroll.setFixedHeight(60)
        summary_scroll.setWidget(self.summary)
        root.addWidget(summary_scroll)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(['账号 / 服务器', '游戏日', '关卡', '场次', '理智', '药 / 临期药 / 石', '掉落', '状态'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        theme.bind(self.table, lambda: 'QTableWidget { background:%s; alternate-background-color:%s;'
                   ' color:%s; border:1px solid %s; border-radius:8px; }'
                   'QHeaderView::section { background:%s; color:%s; border:0; padding:6px; }'
                   % (theme.CARD, theme.ROW_INSET, theme.TEXT, theme.BORDER, theme.ROW_INSET, theme.TEXT_2))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)
        footer = QHBoxLayout()
        note = label('北京时间 04:00 换日，跨日作战计入开始日。仅统计控制台运行期间可归属的记录。'
                     '未落盘或无法识别时显示数据不可用。', 12, color='TEXT_2')
        note.setWordWrap(True)
        footer.addWidget(note, 1)
        close = style_button(PushButton('关闭'), small=True)
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        root.addLayout(footer)
        self.account_filter.currentIndexChanged.connect(self.refresh)
        self.day_filter.currentIndexChanged.connect(self.refresh)
        self.reload()

    @staticmethod
    def _server(server):
        return {'official': '官服', 'bilibili': 'B服'}.get(server, server)

    def reload(self):
        account, day = self.account_filter.currentData(), self.day_filter.currentData()
        self.reports = load_reports(self.history_dir)
        for widget in (self.account_filter, self.day_filter):
            widget.blockSignals(True)
            widget.clear()
        self.account_filter.addItem('全部账号', None)
        identities = sorted({r['server'] + ':' + r['account'] for r in self.reports})
        for identity in identities:
            server, acc = identity.split(':', 1)
            self.account_filter.addItem('%s · %s' % (self.names.get(acc, acc), self._server(server)), identity)
        self.day_filter.addItem('全部游戏日', None)
        days = {row['game_day'] for r in self.reports for row in r['records']}
        days.update(game_day(r['captured_at']) for r in self.reports)
        for value in sorted(days, reverse=True):
            self.day_filter.addItem(value, value)
        self.account_filter.setCurrentIndex(max(0, self.account_filter.findData(account)))
        self.day_filter.setCurrentIndex(max(0, self.day_filter.findData(day)))
        for widget in (self.account_filter, self.day_filter):
            widget.blockSignals(False)
        self.refresh()

    def _drops(self, drops):
        names = {k: v for r in self.reports for k, v in r.get('item_names', {}).items()}
        return '、'.join('%s × %s' % (names.get(item, item), count) for item, count in sorted(drops.items()))

    def refresh(self):
        account, day = self.account_filter.currentData(), self.day_filter.currentData()
        data = summarize(self.reports, account, day)
        if data['status'] == 'available':
            text = '场次 %s · 理智 %s · 药 %s · 临期药 %s · 源石 %s · 掉落 %s' % (
                data['times'], data['sanity'], data['medicinesUsed'], data['expiringMedicinesUsed'],
                data['stonesUsed'], self._drops(data['drops']) or '无')
        else:
            text = '数据不可用（没有可归属的作战记录）'
        if data['unavailable']:
            text += ' · %d 次采集数据不可用' % data['unavailable']
        self.summary.setText(text)
        owners = {row['id']: '%s / %s' % (self.names.get(r['account'], r['account']), self._server(r['server']))
                  for r in self.reports for row in r.get('records', [])}
        self.table.setRowCount(len(data['rows']))
        for i, row in enumerate(data['rows']):
            values = [owners[row['id']], row['game_day'], row['stage'], row['times'], row['sanity'],
                      '%s / %s / %s' % (row['medicinesUsed'], row['expiringMedicinesUsed'], row['stonesUsed']),
                      self._drops(row['drops']),
                      '未完整结束（已计实际增量）' if row['incomplete'] else '完整']
            for j, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(i, j, item)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(6, min(250, self.table.columnWidth(6)))
        self.table.setColumnWidth(0, min(180, self.table.columnWidth(0)))
