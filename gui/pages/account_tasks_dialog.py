"""Graphical per-account targets; editing never writes MAA configuration."""
import copy
import sys
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QCompleter,
    QHBoxLayout, QHeaderView, QLineEdit, QPushButton, QSpinBox,
    QTableWidget, QVBoxLayout, QWidget)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plugins.account_tasks.account_tasks import (ROLES, ROLE_LABELS, load_metadata,
                                                maa_dir_for, normalize_settings)
import theme
from motion import PageStack
from pages.home import navigation
from sheets import SheetDialog
from widgets import label, style_button, style_primary_button


def _combo(values, selected='', editable=False):
    combo = QComboBox()
    combo.setEditable(editable)
    for label, value in values:
        combo.addItem(label, value)
    index = combo.findData(selected)
    if index >= 0:
        combo.setCurrentIndex(index)
    elif editable:
        combo.setCurrentIndex(-1)
        combo.setEditText(str(selected))
    if editable:
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        combo.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
    return combo


def _spin(value, low, high):
    widget = QSpinBox()
    widget.setRange(low, high)
    widget.setValue(int(value))
    return widget


class AccountTasksDialog(SheetDialog):
    def __init__(self, cfg, account, parent=None):
        name = account.get('label') or account.get('id', '')
        super().__init__(parent, title='库存与干员培养',
                         subtitle=f'{name} · 保存后在下次普通挂机时生效', size=(940, 530))
        self.cfg, self.account = cfg, account
        self.original = copy.deepcopy(account.get('maa_tasks') or {})
        self.setWindowTitle('账号任务 · ' + name)
        maa = maa_dir_for(cfg, account.get('server', 'official'))
        self.metadata = load_metadata(maa) if maa else {'items': {}, 'operators': {}, 'activities': [], 'errors': []}
        root = self.body
        self.tabs = navigation(('库存保持', '干员培养', '导入活动目标'), compact=True)
        root.addWidget(self.tabs, 0, Qt.AlignLeft)
        pages = PageStack()
        self.depot = self._section('depot', ['关卡', '材料', '目标库存', '药剂上限', '源石上限'])
        self.progress = self._section('progress', ['干员', '职业', '精英化', '技能等级', '技能 1 专精', '技能 2 专精', '技能 3 专精'])
        pages.addWidget(self.depot['root'])
        pages.addWidget(self.progress['root'])
        pages.addWidget(self._activity_section())
        self.tabs.currentChanged.connect(pages.setCurrentIndex)
        root.addWidget(pages, 1)
        footer = QHBoxLayout()
        self.hint = label('', 12, color='TEXT_2')
        self.hint.setWordWrap(True)
        self.hint.setTextFormat(Qt.PlainText)
        footer.addWidget(self.hint, 1)
        cancel = style_button(QPushButton('取消'))
        self.save_button = style_primary_button(QPushButton('保存账号任务'))
        cancel.clicked.connect(self.reject)
        self.save_button.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(self.save_button)
        root.addLayout(footer)
        for key in ('depot', 'progress'):
            for plan in self.original.get(key, {}).get('plans', []):
                self.add_plan(key, plan)
        theme.bind(pages, lambda: '''
            QTableWidget {background:%s;color:%s;gridline-color:%s;border:1px solid %s;border-radius:8px;}
            QHeaderView::section {background:%s;color:%s;padding:7px;border:0;}
            QLineEdit,QSpinBox,QComboBox {background:%s;color:%s;border:1px solid %s;border-radius:5px;padding:5px;}
            QCheckBox {color:%s;} QCheckBox:disabled {color:%s;}''' % (
            theme.CARD, theme.TEXT, theme.BORDER, theme.BORDER,
            theme.BG, theme.TEXT_2, theme.CARD, theme.TEXT, theme.BORDER, theme.TEXT, theme.TEXT_3))

    def _section(self, key, headers):
        section = self.original.get(key, {})
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(1, 3, 1, 1)
        options = QHBoxLayout()
        enabled = QCheckBox('启用库存保持' if key == 'depot' else '启用干员培养')
        enabled.setChecked(bool(section.get('enabled', False)))
        options.addWidget(enabled)
        options.addStretch()
        sort = dual = None
        if key == 'depot':
            note = label('剿灭优先，其次按列表顺序补库存，再执行普通理智作战。药剂和源石上限为 0 时不消耗。', 12, color='TEXT_2')
            note.setToolTip('每次重新识别当前账号库存。每个计划最多使用 100 份药剂或 100 枚源石。')
            sort = QCheckBox('按库存达成率排序')
            sort.setChecked(bool(section.get('sort_by_progress', False)))
            sort.setEnabled(False)
            sort.setToolTip('暂不可用：排序前需要先确认库存属于当前账号')
            dual = QCheckBox('双芯片折算为芯片组')
            dual.setChecked(bool(section.get('count_dual_chip', False)))
            options.addWidget(sort)
            options.addWidget(dual)
        else:
            note = label('按列表顺序培养，消耗材料与龙门币。专精需要精二和技能 7 级，可能需要多次训练。', 12, color='TEXT_2')
        layout.addLayout(options)
        note.setWordWrap(True)
        layout.addWidget(note)
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setDefaultSectionSize(48)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setMinimumSectionSize(90)
        layout.addWidget(table, 1)
        controls = QHBoxLayout()
        for text, action in [('＋ 添加计划', lambda: self.add_plan(key)),
                              ('移除', lambda: self.remove_plan(key)),
                              ('↑ 上移', lambda: self.move_plan(key, -1)),
                              ('↓ 下移', lambda: self.move_plan(key, 1))]:
            button = style_button(QPushButton(text))
            button.clicked.connect(action)
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        return {'root': widget, 'table': table, 'enabled': enabled, 'sort': sort,
                'dual': dual, 'originals': []}

    def _activity_section(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(1, 3, 1, 1)
        layout.addWidget(label('从本地 MAA 活动资源导入', 15, '600'))
        activity = self.depot['activity'] = QComboBox()
        activity.setAccessibleName('开放活动')
        for name, plans in self.metadata['activities']:
            activity.addItem(name, plans)
        importer = style_button(QPushButton('追加到库存计划'))
        importer.setEnabled(activity.count() > 0)
        importer.clicked.connect(self._import_activity)
        row = QHBoxLayout()
        row.addWidget(activity, 1)
        row.addWidget(importer)
        layout.addLayout(row)
        if activity.count():
            message = '活动目标追加在现有计划末尾，默认库存为 200。请在库存保持页检查目标并按需启用。'
        elif self.metadata['errors']:
            message = '本地资源读取异常：' + '；'.join(self.metadata['errors'])
        else:
            message = '本地资源中没有可导入的开放活动。'
        status = label(message, 12, color='TEXT_2')
        status.setTextFormat(Qt.PlainText)
        status.setWordWrap(True)
        layout.addWidget(status)
        layout.addStretch()
        return widget

    def _part(self, key):
        return self.depot if key == 'depot' else self.progress

    def add_plan(self, key, plan=None):
        part = self._part(key)
        table = part['table']
        plan = copy.deepcopy(plan or {})
        row = table.rowCount()
        table.insertRow(row)
        part['originals'].append(plan)
        if key == 'depot':
            stage = QLineEdit(str(plan.get('stage', '')))
            stage.setPlaceholderText('如 1-7')
            material = _combo([(item['name'] + ' · ' + identity, identity)
                               for identity, item in sorted(self.metadata['items'].items(), key=lambda pair: pair[1].get('sortId', 0))],
                              plan.get('drop_id', ''), True)
            material.lineEdit().setPlaceholderText('搜索材料名 / 输入 ID')
            widgets = [stage, material, _spin(plan.get('target', 200), 1, 100000000),
                       _spin(plan.get('medicine', 0), 0, 100), _spin(plan.get('stone', 0), 0, 100)]
        else:
            operator = _combo([(name, name) for name in sorted(self.metadata['operators'])], plan.get('name', ''), True)
            operator.lineEdit().setPlaceholderText('搜索干员名')
            role = _combo(list(zip(ROLE_LABELS, ROLES)), plan.get('role', 'Unknown'))
            elite = _combo([('不设置', 0), ('精一', 1), ('精二', 2)], plan.get('elite', 0))
            mastery = plan.get('mastery', [0, 0, 0])
            widgets = [operator, role, elite, _spin(plan.get('skill_level', 0), 0, 7),
                       *[_spin(level, 0, 3) for level in mastery]]
            operator.currentTextChanged.connect(lambda name, combo=role: self._operator_role(name, combo))
        for column, widget in enumerate(widgets):
            table.setCellWidget(row, column, widget)
        table.selectRow(row)

    def _operator_role(self, name, role):
        metadata = self.metadata['operators'].get(name)
        if metadata:
            role.setCurrentIndex(role.findData(metadata['role']))

    def plans(self, key):
        part = self._part(key)
        table = part['table']
        result = []
        for row in range(table.rowCount()):
            widgets = [table.cellWidget(row, col) for col in range(table.columnCount())]
            plan = copy.deepcopy(part['originals'][row])
            if key == 'depot':
                material = widgets[1]
                identity = material.currentData() if material.currentIndex() >= 0 and material.currentText() == material.itemText(material.currentIndex()) else material.currentText().strip()
                item = self.metadata['items'].get(identity, {})
                if not item:
                    match = next(((k, v) for k, v in self.metadata['items'].items() if v['name'] == identity), None)
                    if match:
                        identity, item = match
                plan.update(stage=widgets[0].text().strip(), drop_id=identity,
                            drop_name=item.get('name', plan.get('drop_name', '')),
                            target=widgets[2].value(), medicine=widgets[3].value(), stone=widgets[4].value())
            else:
                plan.update(name=widgets[0].currentText().strip(), role=widgets[1].currentData(),
                            elite=widgets[2].currentData(), skill_level=widgets[3].value(),
                            mastery=[widget.value() for widget in widgets[4:7]])
            result.append(plan)
        return result

    def remove_plan(self, key):
        part = self._part(key)
        row = part['table'].currentRow()
        if row >= 0:
            part['table'].removeRow(row)
            part['originals'].pop(row)

    def move_plan(self, key, delta):
        part = self._part(key)
        table = part['table']
        row, target = table.currentRow(), table.currentRow() + delta
        if row < 0 or not 0 <= target < table.rowCount():
            return
        plans = self.plans(key)
        plans[row], plans[target] = plans[target], plans[row]
        table.setRowCount(0)
        part['originals'].clear()
        for plan in plans:
            self.add_plan(key, plan)
        table.selectRow(target)

    def _import_activity(self):
        choice = self.depot['activity']
        plans = choice.currentData() or []
        for plan in plans:
            self.add_plan('depot', plan)
        self.tabs.setCurrentIndex(0)
        self.hint.setText('已追加「%s」的 %d 个目标；请检查目标库存和顺序后保存。' % (choice.currentText(), len(plans)))

    def value(self):
        value = copy.deepcopy(self.original)
        for key in ('depot', 'progress'):
            part = self._part(key)
            value[key] = {**value.get(key, {}), 'enabled': part['enabled'].isChecked(), 'plans': self.plans(key)}
        value['depot'].update(sort_by_progress=False, count_dual_chip=self.depot['dual'].isChecked())
        normalized = normalize_settings(value)
        for key in ('depot', 'progress'):
            normalized[key]['plans'] = [{**original, **plan} for original, plan in zip(value[key]['plans'], normalized[key]['plans'])]
        return normalized

    def _save(self):
        try:
            value = self.value()
        except ValueError as error:
            self.hint.setText('保存失败：%s' % error)
            return
        old = copy.deepcopy(self.account.get('maa_tasks'))
        existed = 'maa_tasks' in self.account
        self.account['maa_tasks'] = value
        try:
            import config as appconfig
            appconfig.save(self.cfg)
        except (OSError, ValueError) as error:
            if existed:
                self.account['maa_tasks'] = old
            else:
                self.account.pop('maa_tasks', None)
            self.hint.setText('保存失败：%s' % error)
            return
        self.accept()


def show_account_tasks_dialog(cfg, account, parent=None):
    return AccountTasksDialog(cfg, account, parent).exec()
