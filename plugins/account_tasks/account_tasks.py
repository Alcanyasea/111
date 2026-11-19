"""Per-account MAA v6.22 task adapter; no filesystem access on import.

Default remains the user's configuration. The disposable console scheme is
rebuilt from Default on every account; copied stock/progression tasks are disabled.
MAA's shared DepotData is reset before launching a new process: v6.22 preserves
old data on failed recognition, but stops target fights when no snapshot exists.
"""
import argparse
import copy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import find_account, load_config, maa_dir_for
from maa_compat import maa_running
from maa_config_io import read_object, write_transaction

SCHEME = '控制台账号任务'
PREFIX = '控制台托管：'
MANAGED_TYPES = ('DepotMaintainTask', 'OperProgressTask')
ROLES = ('Unknown', 'Warrior', 'Pioneer', 'Medic', 'Tank', 'Sniper', 'Caster', 'Support', 'Special')
ROLE_LABELS = ('自动', '近卫', '先锋', '医疗', '重装', '狙击', '术师', '辅助', '特种')


def integer(value, low, high, label):
    if isinstance(value, bool):
        raise ValueError('%s 必须为整数' % label)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError('%s 必须为整数' % label) from None
    if str(number) != str(value).strip() or not low <= number <= high:
        raise ValueError('%s 必须在 %d–%d 之间' % (label, low, high))
    return number


def text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ValueError('%s 不能为空且不得超过 100 字' % label)
    return value.strip()


def normalize_settings(value):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('maa_tasks 必须为对象')
    result = copy.deepcopy(value)
    for key in ('depot', 'progress'):
        section = value.get(key, {})
        if not isinstance(section, dict):
            raise ValueError('%s 必须为对象' % key)
        enabled = section.get('enabled', False)
        if not isinstance(enabled, bool):
            raise ValueError('enabled 必须为布尔值')
        plans = section.get('plans', [])
        if not isinstance(plans, list) or len(plans) > 100:
            raise ValueError('计划必须为列表，最多 100 项')
        normalized = []
        for plan in plans:
            if not isinstance(plan, dict):
                raise ValueError('计划必须为对象')
            if key == 'depot':
                stage = text(plan.get('stage'), '关卡')
                if not re.fullmatch(r'[A-Za-z0-9-]+', stage) or stage.lower() in ('an', 'annihilation'):
                    raise ValueError('库存保持需要具体普通关卡，不能使用剿灭')
                drop_id = text(plan.get('drop_id'), '材料 ID')
                if not re.fullmatch(r'[A-Za-z0-9_]+', drop_id):
                    raise ValueError('请选择材料，或输入有效的材料 ID')
                normalized.append({'stage': stage, 'drop_id': drop_id,
                    'drop_name': str(plan.get('drop_name', ''))[:100],
                    'target': integer(plan.get('target'), 1, 100000000, '库存目标'),
                    'medicine': integer(plan.get('medicine', 0), 0, 100, '药剂数量'),
                    'stone': integer(plan.get('stone', 0), 0, 100, '源石数量')})
            else:
                role = plan.get('role', 'Unknown')
                if role not in ROLES:
                    raise ValueError('干员职业无效')
                elite = integer(plan.get('elite', 0), 0, 2, '精英化')
                skill = integer(plan.get('skill_level', 0), 0, 7, '技能等级')
                mastery = plan.get('mastery', [0, 0, 0])
                if not isinstance(mastery, list) or len(mastery) != 3:
                    raise ValueError('技能专精必须为三个等级')
                mastery = [integer(n, 0, 3, '专精等级') for n in mastery]
                if any(mastery) and (elite != 2 or skill != 7):
                    raise ValueError('设置专精需要精二且技能 7 级')
                if skill > 4 and elite < 1:
                    raise ValueError('技能 5–7 级需要至少精一')
                if any(mastery) and skill == 0:
                    raise ValueError('设置专精需要技能等级 7')
                normalized.append({'name': text(plan.get('name'), '干员姓名'), 'role': role,
                    'elite': elite, 'skill_level': skill, 'mastery': mastery})
        normalized = [{**copy.deepcopy(original), **plan} for original, plan in zip(plans, normalized)]
        if enabled and not normalized:
            raise ValueError('启用的%s必须至少有一个计划' % ('库存保持' if key == 'depot' else '干员培养'))
        result[key] = {**copy.deepcopy(section), 'enabled': enabled, 'plans': normalized}
        if key == 'depot':
            for option in ('sort_by_progress', 'count_dual_chip'):
                flag = section.get(option, False)
                if not isinstance(flag, bool):
                    raise ValueError('%s 必须为布尔值' % option)
                result[key][option] = flag
            if result[key]['sort_by_progress']:
                raise ValueError('按库存达成率排序暂不可用：排序前需要先确认库存属于当前账号')
    return result


def managed_tasks(settings):
    result = []
    depot = settings['depot']
    if depot['enabled']:
        plans = [{'Stage': p['stage'], 'DropId': p['drop_id'], 'DropCount': p['target'],
                  'UseMedicine': p['medicine'] > 0, 'MedicineCount': p['medicine'],
                  'UseStone': p['stone'] > 0, 'StoneCount': p['stone']} for p in depot['plans']]
        result.append({'$type': 'DepotMaintainTask', 'Name': PREFIX + '库存保持', 'IsEnable': True,
            'UpdateDepot': True, 'IsStageManually': False, 'SortByProgress': depot['sort_by_progress'],
            'CountDualChipAsChipPack': depot['count_dual_chip'], 'UseAutoSeries': False,
            'UseMedicine': any(p['UseMedicine'] for p in plans),
            'UseStone': any(p['UseStone'] for p in plans), 'UseExpiringMedicine': False,
            'SkipDuringActivity': False, 'SkipDuringResourceCollection': False,
            'OnlyFirstInsufficientPlan': False, 'PlanList': plans})
    progress = settings['progress']
    if progress['enabled']:
        result.append({'$type': 'OperProgressTask', 'Name': PREFIX + '干员培养', 'IsEnable': True,
            'Plans': [{'Role': p['role'], 'Name': p['name'], 'Elite': p['elite'],
                       'SkillLevel': p['skill_level'], 'SkillMastery': p['mastery'],
                       'ShowRole': p['role'] != 'Unknown'} for p in progress['plans']]})
    return result


def is_annihilation(task):
    return task.get('$type') == 'FightTask' and any(
        str(stage).lower() in ('annihilation', 'an', '剿灭')
        for stage in task.get('StagePlan', []))


def build_configuration(data, value):
    settings = normalize_settings(value)
    result = copy.deepcopy(data)
    configs = result.get('Configurations')
    if not isinstance(configs, dict):
        raise ValueError('MAA Configurations 不存在')
    active = any(settings[key]['enabled'] for key in ('depot', 'progress'))
    if not active and SCHEME not in configs:
        return result
    default = configs.get('Default')
    if not isinstance(default, dict) or not isinstance(default.get('TaskQueue'), list):
        raise ValueError('MAA Default 任务队列缺失')
    scheme = copy.deepcopy(default)
    queue = scheme['TaskQueue']
    if not all(isinstance(t, dict) for t in queue):
        raise ValueError('MAA 任务格式无效')
    for task in queue:
        if task.get('$type') in MANAGED_TYPES:
            task['IsEnable'] = False
    # Existing annihilation stays ahead; ordinary fights yield sanity to stock.
    annihilation = [i for i, t in enumerate(queue) if is_annihilation(t)]
    lower = max(annihilation, default=-1) + 1
    ordinary = [i for i, t in enumerate(queue) if t.get('$type') == 'FightTask' and not is_annihilation(t)]
    insertion = next((i for i in ordinary if i >= lower), len(queue))
    if settings['depot']['enabled'] and any(i < lower for i in ordinary):
        # A mixed order cannot satisfy both requirements without moving user tasks.
        raise ValueError('普通作战位于剿灭之前，请先调整 Default 为剿灭优先')
    queue[insertion:insertion] = managed_tasks(settings)
    configs[SCHEME] = scheme
    result['Current'] = SCHEME
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def check_stopped(maa_dir):
    if maa_running(maa_dir):
        raise ValueError('MAA 仍在运行，不能更新账号任务或库存缓存')


def preflight(maa_dir, value):
    settings = normalize_settings(value)
    data = read_object(Path(maa_dir) / 'config/gui.new.json')
    build_configuration(data, settings)
    if not any(s['enabled'] for s in (settings['depot'], settings['progress'])) and SCHEME not in data.get('Configurations', {}):
        return '未启用账号任务，跳过'
    check_stopped(maa_dir)
    return '账号任务预检通过'


def apply(maa_dir, value):
    settings = normalize_settings(value)
    maa_dir = Path(maa_dir)
    gui = maa_dir / 'config/gui.new.json'
    data = read_object(gui)
    result = build_configuration(data, settings)
    if result == data and not any(settings[k]['enabled'] for k in ('depot', 'progress')):
        return '未启用账号任务，跳过'
    check_stopped(maa_dir)
    cache = maa_dir / 'data/DepotData.json'
    writes = [(gui, result)]
    if settings['depot']['enabled']:
        # Empty cache is essential, not UpdateDepot alone: failure retains old data.
        writes.insert(0, (cache, {'done': False, 'data': {}}))
    write_transaction(writes, before_replace=lambda: check_stopped(maa_dir))
    return '账号任务已隔离；库存将在本次启动重新识别' if settings['depot']['enabled'] else '账号任务已隔离'


def activity_presets(metadata, items, now=None):
    """Parse actual MAA cache/gui/StageActivityV2.json Official activity schema."""
    now = now or datetime.now(timezone.utc)
    groups = metadata.get('Official', {}).get('sideStoryStage', {})
    result = []
    for key, group in groups.items():
        try:
            activity = group.get('Activity', group.get('activity', {}))
            tz = timezone(timedelta(hours=int(activity['TimeZone'])))
            start = datetime.strptime(activity['UtcStartTime'], '%Y/%m/%d %H:%M:%S').replace(tzinfo=tz)
            end = datetime.strptime(activity['UtcExpireTime'], '%Y/%m/%d %H:%M:%S').replace(tzinfo=tz)
            if not start <= now <= end:
                continue
            plans = [{'stage': s['Value'], 'drop_id': s['Drop'],
                      'drop_name': items[s['Drop']].get('name', s['Drop']), 'target': 200,
                      'medicine': 0, 'stone': 0} for s in group.get('Stages', group.get('stages', []))
                     if s.get('Drop') in items and s.get('Value')]
            if plans:
                result.append((activity.get('StageName', key), plans))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return result


def load_metadata(maa_dir):
    """Read MAA public resources only; no config or cached recognition data."""
    root = Path(maa_dir)
    result = {'items': {}, 'operators': {}, 'activities': [], 'errors': []}
    for key, path in (('items', root / 'resource/item_index.json'),
                      ('operators', root / 'resource/battle_data.json')):
        try:
            data = read_object(path)
            if key == 'operators':
                professions = {'WARRIOR': 'Warrior', 'PIONEER': 'Pioneer', 'MEDIC': 'Medic',
                               'TANK': 'Tank', 'SNIPER': 'Sniper', 'CASTER': 'Caster',
                               'SUPPORT': 'Support', 'SPECIAL': 'Special'}
                result[key] = {char['name']: {'role': professions[char['profession']],
                                            'rarity': char.get('rarity', 6)}
                               for identity, char in data.get('chars', {}).items()
                               if identity.startswith('char_') and isinstance(char, dict)
                               and char.get('profession') in professions and char.get('name')}
            else:
                result[key] = {identity: item for identity, item in data.items()
                               if isinstance(item, dict) and isinstance(item.get('name'), str)}
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            result['errors'].append(str(error))
    cache_errors = []
    for path in (root / 'cache/gui/StageActivityV2.json', root / 'cache/gui/StageActivityV2.json.api2'):
        try:
            metadata = read_object(path)
            groups = metadata.get('Official', {}).get('sideStoryStage', {})
            if not isinstance(groups, dict):
                raise ValueError(f'{path.name} 的 sideStoryStage 格式无效')
            result['activities'] = activity_presets(metadata, result['items'])
            break
        except (OSError, ValueError, TypeError, AttributeError) as error:
            cache_errors.append(str(error))
    else:
        result['errors'].extend(cache_errors)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='每账号库存保持与干员培养')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('apply', 'preflight'):
        command = sub.add_parser(name)
        command.add_argument('--config', required=True)
        command.add_argument('--account', required=True)
        command.add_argument('--server', choices=('official', 'bilibili'), required=True)
    restore = sub.add_parser('restore')
    restore.add_argument('--config', required=True)
    restore.add_argument('--account', required=False, default='')
    restore.add_argument('--server', choices=('official', 'bilibili'), required=False, default='official')
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config, required=True)
        root = maa_dir_for(cfg, args.server)
        if root is None:
            raise ValueError('MAA 路径未配置')
        if args.command == 'restore':
            gui = Path(root) / 'config/gui.new.json'
            data = read_object(gui)
            if data.get('Current') == SCHEME:
                configs = data.get('Configurations')
                if not isinstance(configs, dict) or not isinstance(configs.get('Default'), dict):
                    raise ValueError('gui.new.json 缺少 Default，无法恢复账号任务方案')
                check_stopped(root)
                data['Current'] = 'Default'
                write_transaction([(gui, data)], before_replace=lambda: check_stopped(root))
            print('OK 账号任务方案已恢复 Default')
            return 0
        account = find_account(cfg, args.account)
        if account is None:
            raise ValueError('账号不存在')
        action = preflight if args.command == 'preflight' else apply
        print('OK ' + action(root, account.get('maa_tasks')))
        return 0
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        print('ERROR ' + str(error))
        return 2


if __name__ == '__main__':
    sys.exit(main())
