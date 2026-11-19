"""Back up all MAA configuration targets before an atomic multi-file update."""
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path


def read_object(path, *, optional=False):
    """Only a missing optional file is a no-op. Include the filename on parse errors."""
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        if optional:
            return None
        raise
    except ValueError as exc:
        raise ValueError(f'{path} 读取失败：{exc}') from exc
    if not isinstance(value, dict):
        raise ValueError(f'{path} 的 JSON 顶层必须是对象')
    return value


def _stage(path, content, suffix):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name+'.', suffix=suffix, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def write_transaction(writes, *, before_replace=None):
    """Exact-byte rollback on failure; backup failure leaves all originals intact."""
    token = uuid.uuid4().hex
    originals, staged, applied = [], [], []
    try:
        for target, data in writes:
            path = Path(target)
            original = path.read_bytes() if path.exists() else None
            if original is not None:
                backup = path.with_name(path.name+'.maa-console-'+token+'.bak')
                shutil.copy2(path, backup)
                if backup.read_bytes() != original:
                    raise OSError('MAA configuration backup verification failed')
            originals.append((path, original))
            payload = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
            staged.append((path, _stage(path, payload, '.pending')))
        if before_replace is not None:
            before_replace()
        for path, temp in staged:
            os.replace(temp, path)
            applied.append(path)
    except BaseException:
        for path, original in reversed(originals):
            if path not in applied:
                continue
            if original is None:
                path.unlink(missing_ok=True)
            else:
                temp = _stage(path, original, '.rollback')
                try:
                    os.replace(temp, path)
                finally:
                    temp.unlink(missing_ok=True)
        raise
    finally:
        for _, temp in staged:
            temp.unlink(missing_ok=True)
