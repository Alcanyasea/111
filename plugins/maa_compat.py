"""Read-only MAA installation capability checks. No filesystem work on import."""
import subprocess
import json
import ntpath
from pathlib import Path


def maa_running(maa_dir=None):
    """Check the target installation; an unreadable process path fails closed."""
    try:
        result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq MAA.exe', '/FO', 'CSV', '/NH'],
                                capture_output=True, text=True, check=True,
                                creationflags=0x08000000, timeout=15)
        if 'maa.exe' not in result.stdout.lower():
            return False
        if maa_dir is None:
            return True
        # 固定脚本，不把用户路径插进 PowerShell；路径仅在 Python 中比较。
        command = (
            "$ErrorActionPreference='Stop'; "
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
            "ConvertTo-Json -Compress -InputObject @(Get-CimInstance Win32_Process "
            "-Filter \"Name = 'MAA.exe'\" | Select-Object ExecutablePath)"
        )
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
            capture_output=True, text=True, encoding='utf-8', check=True,
            creationflags=0x08000000, timeout=15)
        processes = json.loads(result.stdout)
        if not isinstance(processes, list):
            raise ValueError('MAA process list is not an array')
        # 解析目录链接与短路径；不同安装可以硬链接同一个 exe，配置归属由目录决定。
        target = ntpath.normcase(str(Path(maa_dir).resolve(strict=True)))
        for process in processes:
            path = process.get('ExecutablePath') if isinstance(process, dict) else None
            if not isinstance(path, str) or not path.strip():
                raise ValueError('无法读取运行中 MAA 的路径')
            if ntpath.normcase(str(Path(path).parent.resolve(strict=True))) == target:
                return True
        return False
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
        raise ValueError('无法确认 MAA 已退出，停止修改配置') from exc


def ensure_maa_stopped(maa_dir=None):
    """CLI preflight: fail closed if Windows cannot enumerate running MAA."""
    if maa_dir is not None and not (Path(maa_dir) / 'MAA.exe').exists():
        return
    if maa_running(maa_dir):
        raise ValueError('MAA 仍在运行，停止修改配置')
