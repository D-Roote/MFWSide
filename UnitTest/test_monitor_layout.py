"""Dashboard gaps and scrollable content must fit even at minimum width."""
import os
from pathlib import Path
import unittest

import test_runtime_lifecycle as ui_fixtures
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QComboBox, QToolButton, QWidget


class MonitorPreviewLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui_fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = ui_fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.screen = self.fixture.show_screen_panel()

    def assert_fits_viewport(self):
        QApplication.processEvents()
        QApplication.processEvents()
        scroll = self.window.ui.monitorScrollArea
        self.assertEqual(scroll.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())
        for child in (self.screen.preview, self.screen.status, self.screen.capture_button):
            left = child.mapTo(scroll.viewport(), QPoint()).x()
            self.assertGreaterEqual(left, 0)
            self.assertLessEqual(left + child.width(), scroll.viewport().width())

    def test_long_warning_and_portrait_landscape_images_fit_minimum_width(self):
        self.window.resize(1100, 900)
        self.window.ui.workspaceSplitter.setSizes([900, 309])
        for text in (
                "실행 캐시 작업 화면 갱신 주기에 따라 표시  목표 60 FPS  출력 40.0 FPS\n출력 프레임이 낮습니다. 목표 프레임을 낮추세요.",
                "작업대상연결확인실패" * 60):
            self.screen.status.setText(text)
            for width, height in ((2048, 1024), (512, 1536)):
                image = QImage(width, height, QImage.Format.Format_RGB32)
                image.fill(0)
                self.screen.preview.set_image(image)
                self.assert_fits_viewport()
                self.assertTrue(self.screen.preview.rect().contains(self.screen.preview.image_rect()))

    def test_height_resize_and_card_reorder_keep_horizontal_scroll_zero(self):
        sections = self.window.ui.monitorSectionsWidget
        self.screen.status.setText("연결 준비 재시도 중  목표 60 FPS  출력 0.0 FPS\n출력 프레임이 낮습니다. 목표 프레임을 낮추세요.")
        for order in (("screen", "log", "connection"), ("connection", "screen", "log")):
            cards = {card.property("monitorSectionKey"): card for card in sections.sections()}
            for index, key in enumerate(order):
                sections.splitter.insertWidget(index, cards[key])
            for height in (500, 1000):
                self.window.resize(1100, height)
                self.window.ui.workspaceSplitter.setSizes([900, 309])
                sections.sync_section_layout()
                self.assert_fits_viewport()

    def test_expand_after_long_status_does_not_enlarge_monitor_content(self):
        section = self.screen.parentWidget().parentWidget()
        toggle = section.findChild(QToolButton, "monitorSectionToggle")
        toggle.setChecked(False)
        self.screen.status.setText("테스트 캡처  목표 60 FPS  출력 0.0 FPS\n출력 프레임이 낮습니다. 목표 프레임을 낮추세요.")
        self.window.resize(1100, 650)
        self.window.ui.workspaceSplitter.setSizes([900, 309])
        toggle.setChecked(True)
        self.assert_fits_viewport()

    def assert_workspace_gaps_match(self):
        ui = self.window.ui
        left = ui.mainTab.findChild(QWidget, "_1_settingStartWidget")
        middle, right = ui.workspaceSplitter.widget(0), ui.workspaceSplitter.widget(1)
        left_gap = middle.mapTo(ui.mainTab, QPoint()).x() - left.mapTo(ui.mainTab, QPoint()).x() - left.width()
        right_gap = right.mapTo(ui.mainTab, QPoint()).x() - middle.mapTo(ui.mainTab, QPoint()).x() - middle.width()
        self.assertEqual(left_gap, right_gap)
        self.assertEqual(right_gap, ui.workspaceSplitter.handleWidth())
        self.assertEqual(left.width(), 312)
        self.assertGreaterEqual(middle.width(), 300)
        self.assertGreaterEqual(right.width(), 309)

    def test_equal_workspace_gaps_survive_theme_minimum_width_and_splitter_moves(self):
        for theme in ("light", "dark"):
            self.window.settings_panel.theme_combo.setCurrentIndex(self.window.settings_panel.theme_combo.findData(theme))
            for width in (self.window.minimumWidth(), 1100, 1400):
                self.window.resize(width, 780)
                for sizes in ([900, 309], [300, 900]):
                    self.window.ui.workspaceSplitter.setSizes(sizes)
                    QApplication.processEvents()
                    self.assert_workspace_gaps_match()
                    self.assertEqual(self.window.width(), width)
                    self.assert_fits_viewport()
            directory = os.environ.get("MAABA_UI_PREVIEW_DIR")
            if directory:
                Path(directory).mkdir(parents=True, exist_ok=True)
                self.window.ui.mainTab.grab().save(str(Path(directory) / f"workspace_gaps_{theme}.png"))

    def test_minimum_width_details_and_preview_never_gain_horizontal_scroll(self):
        window = self.window
        window.show_sub_cases(self.fixture.find_task_widget(window))
        combos = window.ui.scrollSettingContents.findChildren(QComboBox, "optionSelect")
        self.assertTrue(combos)
        for combo in combos:
            self.assertGreater(combo.count(), 0)
            combo.setItemText(0, "긴 세부 설정 선택 항목 " * 20)
        self.screen.status.setText("출력 프레임이 낮습니다. 목표 프레임을 낮추세요. " * 8)
        for theme in ("light", "dark"):
            window.settings_panel.theme_combo.setCurrentIndex(window.settings_panel.theme_combo.findData(theme))
            window.resize(window.minimumWidth(), 700)
            QApplication.processEvents()
            self.assert_workspace_gaps_match()
            self.assert_fits_viewport()
            scroll = window.ui.scrollSettingWidget
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())
            directory = os.environ.get("MAABA_UI_PREVIEW_DIR")
            if directory:
                Path(directory).mkdir(parents=True, exist_ok=True)
                window.ui.mainTab.grab().save(str(Path(directory) / f"workspace_minimum_{theme}.png"))
