# -*- coding: utf-8 -*-
r"""插件公共工具：config 读取 / 账号定位 / MAA 目录 / 原子 JSON 写 / 日志。

此前这批函数在五个插件（base_schedule / fiammetta / fight_stage /
farm_guard / infrast_collect）里逐字复制、只有日志标签不同——改一处漏四处
的温床，2026-09 的几次线上事故（漏刷、房间被禁用）都与维护不同步有关。
收敛到这里统一维护；各插件通过：

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from common import ...

使用（插件被 master.ps1 以脚本方式直调，脚本目录才在 sys.path 里，
所以要先把 plugins\\ 目录插进去）。
"""
import json
import os
from datetime import datetime
from pathlib import Path

DEFAULT_CONFIG = Path(r"D:\1\config.json")


def load_config(path):
    """读 config.json；文件缺失/损坏返回 {}（各插件自行判空兜底）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def read_json(path):
    """读任意 JSON 文件；缺失/损坏/被占用返回 None，不抛异常。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def find_account(cfg, acc_id):
    """按 id 在 config.accounts 数组里找账号，找不到返回 None。"""
    for a in cfg.get("accounts") or []:
        if isinstance(a, dict) and a.get("id") == acc_id:
            return a
    return None


def maa_dir_for(cfg, server):
    """按服务器（official/bilibili）取 MAA 目录，未配置返回 None。"""
    key = "maa_bilibili_dir" if server == "bilibili" else "maa_official_dir"
    d = (cfg.get("paths") or {}).get(key)
    return Path(d) if d else None


def atomic_json_write(path, data):
    """原子写入 JSON（临时文件 + os.replace，中文不转义）；自动建父目录。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


def log_file(cfg, tag, msg):
    """往 master_log.txt 追加一行（tag 区分来源插件），失败静默。"""
    try:
        lp = (cfg.get("paths") or {}).get("log_file")
        if not lp:
            return
        with open(lp, "a", encoding="utf-8") as f:
            f.write("%s - [%s] %s\n" % (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tag, msg))
    except OSError:
        pass
