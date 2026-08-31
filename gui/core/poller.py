# -*- coding: utf-8 -*-
"""后台轮询线程：把耗时的系统命令移出界面线程。

计划任务查询（Get-ScheduledTask）和 ADB 设备检查（adb devices）偶尔会卡几秒，
放在主线程会导致控制台「点击没反应」。这里用 QThread 后台跑，结果通过信号回传。
"""
from PySide6.QtCore import QThread, Signal

from core import adb, scheduler


class SchedulerPoller(QThread):
    """后台查询计划任务状态，每 30 秒一次（启动后立即查一次）。"""

    result = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        self.result.emit(scheduler.query())
        while not self._stop_flag:
            for _ in range(60):  # 60 × 0.5s = 30s
                if self._stop_flag:
                    return
                self.msleep(500)
            self.result.emit(scheduler.query())


class AdbPoller(QThread):
    """后台检查 ADB 设备是否在线，每 15 秒一次。"""

    result = Signal(object)  # True / False / None（未知）

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        while not self._stop_flag:
            self.result.emit(adb.is_connected(self.cfg))
            for _ in range(30):  # 30 × 0.5s = 15s
                if self._stop_flag:
                    return
                self.msleep(500)
