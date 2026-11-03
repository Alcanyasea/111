"""GUI 与插件共用的项目路径规则；不探测外部软件、不写文件。"""
from pathlib import Path


def relocate_project_paths(cfg, config_path):
    """就地更新脚本路径及原默认日志路径，保留自定义日志和外部软件路径。

    script_dir 属于项目结构，即使旧副本仍存在也不能继续使用它。
    与 scripts/master_log.txt 不同的日志位置视为用户显式指定。
    """
    if not isinstance(cfg, dict):
        return cfg
    paths = cfg.get("paths")
    if not isinstance(paths, dict):
        paths = cfg["paths"] = {}
    scripts = Path(config_path).resolve().parent / "scripts"
    old_scripts = paths.get("script_dir")
    old_log = paths.get("log_file")
    if not old_log or (old_scripts and Path(old_log) == Path(old_scripts) / "master_log.txt"):
        paths["log_file"] = str(scripts / "master_log.txt")
    paths["script_dir"] = str(scripts)
    return cfg
