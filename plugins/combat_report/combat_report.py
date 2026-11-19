"""Read-only MAA v6.22 DailyReport capture, scoped to a console account attempt.

Source sessions have no account identifier. Never import historical sessions: only
sessions starting after the boundary belong to this attempt. Game days use UTC+8
04:00; a session spanning that boundary is assigned to its start day because MAA
does not provide per-battle timestamps. Console output is independent of gameplay.
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import atomic_json_write

SHANGHAI = timezone(timedelta(hours=8))
COUNTERS = ('times', 'rounds', 'sanity', 'medicinesUsed',
            'expiringMedicinesUsed', 'stonesUsed')


def timestamp(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('日报时间缺少时区')
    return dt.astimezone(timezone.utc)


def game_day(value):
    return (timestamp(value).astimezone(SHANGHAI) - timedelta(hours=4)).date().isoformat()


def session_key(session):
    return str(session['stage']) + '|' + timestamp(session['startTime']).isoformat()


def read_snapshot(maa_dir, since=None):
    """Missing source is explicit unavailable; no filesystem mutation or probing elsewhere."""
    folder = Path(maa_dir) / 'data' / 'DailyReport'
    result = {'status': 'unavailable', 'sessions': {}, 'reason': 'MAA 尚无可读取的作战日报'}
    try:
        files = [path for path in sorted(folder.glob('*.json'))
                 if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', path.name)]
        if since is not None:
            # MAA filenames use game days. Include the preceding day as a
            # conservative margin for timezone/date-boundary versions.
            earliest = (timestamp(since).astimezone(SHANGHAI) - timedelta(days=1, hours=4)).date().isoformat()
            files = [path for path in files if path.stem >= earliest]
        if not files:
            return result
        sessions = {}
        for path in files:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', path.name):
                continue
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('sessions'), list):
                raise ValueError('不支持的日报版本或结构')
            for raw in data['sessions']:
                s = dict(raw)
                if not isinstance(s.get('stage'), str) or not s['stage']:
                    raise ValueError('关卡字段无效')
                # A parseable JSON object with omitted counters is still a
                # damaged native session.  Treat it as unavailable instead
                # of manufacturing zeroes that could look like a real run.
                if any(field not in s for field in COUNTERS):
                    raise ValueError('日报计数字段缺失')
                key = session_key(s)
                for field in COUNTERS:
                    value = s[field]
                    if type(value) is not int or value < 0:
                        raise ValueError('日报计数无效')
                    s[field] = value
                if not isinstance(s.get('drops'), dict) or any(
                        type(v) is not int or v < 0 for v in s['drops'].values()):
                    raise ValueError('掉落结构无效')
                if s.get('endTime'):
                    if timestamp(s['endTime']) < timestamp(s['startTime']):
                        raise ValueError('日报时间顺序无效')
                if key in sessions and sessions[key] != s:
                    raise ValueError('日报会话标识冲突')
                sessions[key] = s
        return {'status': 'available', 'sessions': sessions, 'reason': ''}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return {'status': 'unavailable', 'sessions': {}, 'reason': str(exc)}


def capture(maa_dir, account, server, run_id, now=None):
    captured_at = (now or datetime.now(timezone.utc)).isoformat()
    return {'version': 1, 'account': str(account), 'server': server,
            'run_id': run_id, 'captured_at': captured_at,
            'source': str(Path(maa_dir).resolve()), 'snapshot': read_snapshot(maa_dir, captured_at)}


def collect(boundary, after, now=None):
    result = {k: boundary[k] for k in ('account', 'server', 'run_id', 'captured_at')}
    result.update(version=1, collected_at=(now or datetime.now(timezone.utc)).isoformat(),
                  status='unavailable', records=[], reason='')
    before = boundary['snapshot']
    if before['status'] != 'available' and before.get('sessions'):
        result['reason'] = '采集前日报不可读'
        return result
    # An absent directory can become valid during the first ever run. A corrupt
    # baseline must never become an empty baseline (would import historical data).
    if before['status'] != 'available' and before.get('reason') != 'MAA 尚无可读取的作战日报':
        result['reason'] = '采集前日报不可读：' + before.get('reason', '')
        return result
    if after['status'] != 'available':
        result['reason'] = after.get('reason', '采集后日报不可读')
        return result
    start = timestamp(boundary['captured_at'])
    end = now or datetime.now(timezone.utc)
    for key, session in after['sessions'].items():
        when = timestamp(session['startTime'])
        if when < start or when > end:
            continue
        previous = before['sessions'].get(key, {})
        record = {field: max(0, session[field] - previous.get(field, 0)) for field in COUNTERS}
        record['drops'] = {k: v - previous.get('drops', {}).get(k, 0)
                           for k, v in session['drops'].items()
                           if v > previous.get('drops', {}).get(k, 0)}
        if not any(record[f] for f in COUNTERS) and not record['drops']:
            continue
        identity = '|'.join((boundary['server'], boundary['account'], boundary['run_id'], key))
        record.update(id=hashlib.sha256(identity.encode()).hexdigest(),
                      stage=session['stage'], startTime=session['startTime'],
                      endTime=session.get('endTime'), incomplete=not bool(session.get('endTime')),
                      game_day=game_day(session['startTime']))
        result['records'].append(record)
    if result['records']:
        result['status'] = 'available'
    else:
        result['reason'] = '本次未发现可归属的新增作战记录（不能据此判断零掉落）'
    return result


def load_reports(history_dir):
    reports = []
    for path in (Path(history_dir) / 'combat_report').glob('*.json'):
        if path.name.endswith('.boundary.json'):
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if (data.get('version') == 1 and isinstance(data.get('records'), list)
                    and data.get('server') in ('official', 'bilibili')
                    and isinstance(data.get('account'), str) and data['account']
                    and data.get('status') in ('available', 'unavailable')):
                timestamp(data['captured_at'])
                if data.get('collected_at') and timestamp(data['collected_at']) < timestamp(data['captured_at']):
                    raise ValueError('报告采集时间顺序无效')
                for row in data['records']:
                    if any(key not in row for key in ('id', 'game_day', 'stage', 'drops', 'incomplete') + COUNTERS):
                        raise ValueError('报告结构无效')
                    if not isinstance(row['drops'], dict):
                        raise ValueError('掉落结构无效')
                    if any(not isinstance(k, str) or not k or type(v) is not int or v < 0
                           for k, v in row['drops'].items()):
                        raise ValueError('掉落计数无效')
                    if any(type(row[key]) is not int or row[key] < 0 for key in COUNTERS):
                        raise ValueError('报告计数无效')
                    if not isinstance(row['stage'], str) or not row['stage']:
                        raise ValueError('报告关卡无效')
                    if not isinstance(row['id'], str) or not row['id'] or type(row['incomplete']) is not bool:
                        raise ValueError('报告标识或结束状态无效')
                    if not isinstance(row['game_day'], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['game_day']):
                        raise ValueError('报告游戏日无效')
                    datetime.strptime(row['game_day'], '%Y-%m-%d')
                    if 'startTime' in row:
                        if game_day(row['startTime']) != row['game_day']:
                            raise ValueError('报告开始时间与游戏日不符')
                        if row.get('endTime') and timestamp(row['endTime']) < timestamp(row['startTime']):
                            raise ValueError('报告结束时间无效')
                if (data['status'] == 'available') != bool(data['records']):
                    raise ValueError('报告可用状态与记录不符')
                if not isinstance(data.get('item_names', {}), dict) or any(
                        not isinstance(k, str) or not isinstance(v, str)
                        for k, v in data.get('item_names', {}).items()):
                    raise ValueError('材料名称无效')
                reports.append(data)
        except (OSError, ValueError, AttributeError, TypeError, KeyError):
            continue
    return reports


def read_item_names(maa_dir, item_ids):
    """Optional metadata copied into console reports; unknown IDs remain visible."""
    try:
        data = json.loads((Path(maa_dir) / 'resource' / 'item_index.json').read_text(encoding='utf-8-sig'))
        return {item: data[item]['name'] for item in item_ids
                if isinstance(data.get(item), dict) and isinstance(data[item].get('name'), str)}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def prune_reports(history_dir, now=None, days=60):
    """Delete only recognized console files, never follow links or touch MAA data."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    for path in (Path(history_dir) / 'combat_report').glob('*.json'):
        if path.is_symlink() or not re.fullmatch(r'[a-f0-9]{64}(?:\.boundary)?\.json', path.name):
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('version') == 1 and timestamp(data['captured_at']) < cutoff:
                path.unlink()
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass


def summarize(reports, account=None, day=None):
    rows, seen = [], set()
    unavailable = 0
    for report in reports:
        identity = report.get('server', '') + ':' + str(report.get('account', ''))
        if account and identity != account:
            continue
        if report.get('status') != 'available':
            if not day or game_day(report['captured_at']) == day:
                unavailable += 1
        for row in report.get('records', []):
            if (day and row['game_day'] != day) or row['id'] in seen:
                continue
            seen.add(row['id'])
            rows.append(row)
    totals = {field: sum(r.get(field, 0) for r in rows) for field in COUNTERS}
    drops = {}
    for row in rows:
        for item, count in row['drops'].items():
            drops[item] = drops.get(item, 0) + count
    return dict(totals, rows=rows, drops=drops, unavailable=unavailable,
                status='available' if rows else 'unavailable')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('boundary', 'collect'))
    for key in ('config', 'account', 'server', 'run-id'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--maa-dir')
    parser.add_argument('--history-dir')
    args = parser.parse_args(argv)
    try:
        cfg = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
        if args.server not in ('official', 'bilibili'):
            raise ValueError('服务器无效')
        maa = args.maa_dir or cfg.get('paths', {}).get('maa_' + args.server + '_dir')
        if not maa:
            raise ValueError('未配置 MAA 目录')
        history = Path(args.history_dir or str(Path(cfg.get('paths', {}).get(
            'script_dir') or Path(args.config).resolve().parent / 'scripts') / 'run_history'))
        # Hash all user-controlled identity components; never use a raw run id as a path.
        name = hashlib.sha256((args.server + '|' + args.account + '|' + args.run_id).encode()).hexdigest()
        folder = history / 'combat_report'
        boundary_path = folder / (name + '.boundary.json')
        if args.action == 'boundary':
            if boundary_path.exists():
                result = json.loads(boundary_path.read_text(encoding='utf-8'))
            else:
                result = capture(maa, args.account, args.server, args.run_id)
                atomic_json_write(boundary_path, result)
            output = {'status': result['snapshot']['status'], 'boundary': str(boundary_path),
                      'records': [], 'reason': result['snapshot'].get('reason', '')}
        else:
            boundary = json.loads(boundary_path.read_text(encoding='utf-8'))
            if boundary['source'] != str(Path(maa).resolve()):
                raise ValueError('MAA 来源与开始边界不一致')
            report_path = folder / (name + '.json')
            if report_path.exists():
                result = json.loads(report_path.read_text(encoding='utf-8'))
            else:
                result = collect(boundary, read_snapshot(maa, boundary['captured_at']))
                result['item_names'] = read_item_names(maa, {item for row in result['records'] for item in row['drops']})
                atomic_json_write(report_path, result)
            output = dict(result, report_path=str(report_path))
            prune_reports(history)
        print(json.dumps(output, ensure_ascii=True, separators=(',', ':')))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({'status': 'unavailable', 'records': [], 'reason': str(exc)}, ensure_ascii=True))
        return 0  # Report collection must never change the gameplay result.


if __name__ == '__main__':
    raise SystemExit(main())
