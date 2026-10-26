"""测试专用的配置导入：禁止 import 时探测真实 MAA 安装目录。"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gui"))
with patch.object(Path, "iterdir", return_value=iter(())):
    import config as appconfig
