# -*- coding: utf-8 -*-
"""SchedulerApplyWorker 行为测试（offscreen，不启动 GUI、不写真实配置）。

覆盖：apply 抛异常时 done 仍必须发出（否则页面按钮永远禁用）、
构造时深拷贝配置（页面之后改原 dict 不影响本次同步）。
"""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from core import poller, scheduler  # noqa: E402


class SchedulerApplyWorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_constructor_deepcopies_cfg(self):
        cfg = {"schedule": {"times": []}}
        worker = poller.SchedulerApplyWorker(cfg)
        cfg["schedule"]["times"].append({"time": "04:00", "enabled": True})
        self.assertEqual(worker.cfg, {"schedule": {"times": []}})

    def test_exception_still_emits_done(self):
        results = []
        with mock.patch.object(scheduler, "apply",
                               side_effect=RuntimeError("boom")):
            worker = poller.SchedulerApplyWorker({"paths": {}})
            worker.done.connect(
                lambda ok, msg, info: results.append((ok, msg, info)))
            worker.start()
            self.assertTrue(worker.wait(5000), "worker 未在 5 秒内结束")
            deadline = time.time() + 5
            while not results and time.time() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
        self.assertEqual(len(results), 1)
        ok, msg, info = results[0]
        self.assertFalse(ok)
        self.assertIn("boom", msg)
        self.assertIsNone(info)


if __name__ == "__main__":
    unittest.main()
