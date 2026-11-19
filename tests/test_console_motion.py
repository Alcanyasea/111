"""界面动效回归，只创建内存中的页面和按钮。"""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tests.test_support import appconfig
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
from motion import PageStack, PressFeedback, press_feedback


class MotionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(appconfig, "save").start()
        self.motion = patch("motion.motion_enabled", return_value=True).start()

    def test_interrupted_navigation_selects_immediately_and_restores_origin(self):
        stack = PageStack()
        for _ in range(3):
            stack.addWidget(QWidget())
        stack.show()
        self.app.processEvents()
        stack.setCurrentIndex(2)
        self.assertEqual(stack.currentIndex(), 2)
        self.assertGreaterEqual(stack._slide.duration(), 500)
        start_x = stack.currentWidget().x()
        self.assertGreaterEqual(start_x, 70)
        stack._slide.setCurrentTime(stack._slide.duration() // 2)
        self.assertTrue(stack._departing.isVisible())
        self.assertFalse(stack._departing.snapshot.isNull())
        self.assertAlmostEqual(stack.currentWidget().x(), start_x / 2, delta=2)
        stack.setCurrentIndex(1)
        self.assertEqual(stack.widget(2).pos(), QPoint())
        self.assertLess(stack.currentWidget().x(), 0)
        self.assertTrue(QSignalSpy(stack._slide.finished).wait(3000))
        self.assertTrue(stack._departing.snapshot.isNull())
        self.assertFalse(stack._departing.isVisible())
        self.assertEqual(stack.currentWidget().pos(), QPoint())
        stack.setCurrentIndex(0)
        stack.resize(380, 220)
        self.app.processEvents()
        self.assertEqual(stack.currentWidget().geometry(), stack.contentsRect())
        self.motion.return_value = False
        stack.setCurrentIndex(2)
        self.assertEqual(stack.currentWidget().pos(), QPoint())
        stack.close()

    def test_mouse_and_keyboard_feedback_preserve_single_click_and_hit_area(self):
        button = press_feedback(QPushButton("运行"))
        press_feedback(button)
        calls = []
        button.clicked.connect(lambda: calls.append(1))
        button.show()
        self.app.processEvents()
        bounds = button.geometry()
        layer = button.findChild(PressFeedback)
        for key in (False, True):
            if key:
                QTest.keyPress(button, Qt.Key.Key_Space)
                QTest.keyRelease(button, Qt.Key.Key_Space)
            else:
                QTest.mousePress(button, Qt.MouseButton.LeftButton)
                self.assertTrue(layer.isVisible())
                QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
            self.assertTrue(layer.isVisible())
            self.assertTrue(QSignalSpy(layer._release.finished).wait(3000))
            self.assertFalse(layer.isVisible())
        self.assertEqual(calls, [1, 1])
        self.assertEqual(button.geometry(), bounds)
        self.motion.return_value = False
        button.click()
        self.assertFalse(layer.isVisible())
        button.setDisabled(True)
        button.click()
        self.assertEqual(len(calls), 3)
        button.close()
