import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QAbstractItemView, QMessageBox
from app.storageManagement import StorageGroup, StorageUsage


class ManagementUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        registration = patch.object(self.window.settings_panel.startup_registration, "is_enabled", return_value=False)
        registration.start()
        self.addCleanup(registration.stop)
        unregister = patch.object(self.window.settings_panel.startup_registration, "set_enabled")
        unregister.start()
        self.addCleanup(unregister.stop)
        self.panel = self.window.settings_panel.management
        self.user = self.window.runtime.user_dir
        self.panel.configure(self.user, self.user / "sandbox", self.user / "sandbox/app",
                             self.user / "sandbox/assets/resources", self.window._can_manage_data)
        self.addCleanup(self.stop)

    def stop(self):
        self.panel.shutdown()
        if self.panel.worker:
            self.panel.worker.wait(5000)
        fixtures.QApplication.processEvents()

    def wait_worker(self):
        for _ in range(200):
            if not self.panel.busy:
                return
            fixtures.QTest.qWait(10)
        self.fail("management worker did not finish")

    def test_management_default_position_and_async_inventory(self):
        self.assertEqual(self.window.settings_panel.section_order()[-2:], ["appearance", "management"])
        directory = self.user / "debug"
        directory.mkdir()
        (directory / "old.log").write_text("example")
        self.panel.scan()
        self.assertFalse(self.window.ui.workStartBtn.isEnabled())
        self.wait_worker()
        self.assertGreater(self.panel.tree.topLevelItemCount(), 0)
        self.assertIn("사용 용량", self.panel.status.text())
        self.assertTrue(self.window.ui.workStartBtn.isEnabled())

    def test_auto_cleanup_defaults_on_and_persists_above_reset(self):
        from app.settingsUI import SettingsStore
        settings = self.window.settings_panel
        self.assertTrue(settings.auto_cleanup_on_exit_enabled())
        row = settings.auto_cleanup_row
        self.assertEqual(row.title_label.text(), "자동 로그 정리")
        self.assertEqual(row.description_label.text(), "앱이 종료될 때 모든 로그와 캐시를 같이 정리합니다.")
        section_layout = settings._section_widgets["management"].layout()
        self.assertIs(row.parentWidget(), settings._section_widgets["management"])
        self.assertEqual(section_layout.indexOf(row) + 1,
                         section_layout.indexOf(self.panel.reset_row))
        settings.auto_cleanup_checkbox.setChecked(False)
        saved = SettingsStore(settings.store.config_path).load()
        self.assertIs(saved["general"]["auto_cleanup_on_exit"], False)
        settings.auto_cleanup_checkbox.setChecked(True)
        saved = SettingsStore(settings.store.config_path).load()
        self.assertIs(saved["general"]["auto_cleanup_on_exit"], True)

    def test_auto_cleanup_description_uses_the_same_bottom_padding_as_other_settings(self):
        self.show_management()
        settings = self.window.settings_panel
        reference = settings.minimize_checkbox.parentWidget()
        row = settings.auto_cleanup_row
        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                settings.theme_combo.setCurrentIndex(settings.theme_combo.findData(theme))
                fixtures.QTest.qWait(20)
                padding = lambda widget: widget.height() - widget.description_label.mapTo(
                    widget, QPoint(0, widget.description_label.height())).y()
                self.assertEqual(padding(row), padding(reference))
                self.assertGreaterEqual(padding(row), 12)
                self.assertEqual(row.parentWidget().layout().spacing(), 0)

    def test_delete_requires_confirmation_and_does_not_reset_settings(self):
        directory = self.user / "debug"
        directory.mkdir()
        log = directory / "old.log"
        log.write_text("example")
        other_directory = self.user / "archive"
        other_directory.mkdir()
        other_log = other_directory / "keep.log"
        other_log.write_text("a larger log which was not selected")
        self.panel.scan()
        self.wait_worker()
        self.panel.tree.sortItems(0, Qt.SortOrder.AscendingOrder)
        for row in range(self.panel.tree.topLevelItemCount()):
            item = self.panel.tree.topLevelItem(row)
            if item.usage.group.key == "debug":
                item.setCheckState(0, Qt.CheckState.Checked)
        self.panel.tree.sortItems(1, Qt.SortOrder.DescendingOrder)
        self.assertEqual([usage.group.key for usage in self.panel._selected()], ["debug"])
        with patch("app.managementUI.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.panel.delete_selected()
        self.assertTrue(log.exists())
        with patch("app.managementUI.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.panel.delete_selected()
        self.wait_worker()
        self.assertFalse(log.exists())
        self.assertTrue(other_log.exists())
        self.assertTrue((self.user / "config/user_config.json").is_file())
        self.assertIn("삭제 완료", self.panel.status.text())

    def usage(self, key, label, size, removable, count=1, errors=None):
        return StorageUsage(StorageGroup(key, label, self.user, self.user / key),
                            bytes=size, removable_bytes=removable, files=count,
                            removable_files=count if removable else 0, errors=errors or [])

    def publish_inventory(self, usages):
        worker = MagicMock(error="", usages=None, result=usages)
        self.panel.worker = worker
        self.panel._finished()

    def show_management(self):
        self.window.resize(self.window.minimumWidth(), 820)
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        panel = self.window.settings_panel
        panel.navigation.setCurrentRow(panel.section_order().index("management"))
        fixtures.QTest.qWait(20)

    def test_inventory_hides_zero_cleanup_sizes_but_keeps_access_warnings(self):
        protected = self.usage("protected", "Protected", 500, 0)
        empty = self.usage("empty", "Empty", 0, 0, errors=["denied"])
        removable = self.usage("logs", "Logs", 800, 300)
        self.publish_inventory([protected, empty, removable])
        self.assertEqual(self.panel.usages, [removable])
        self.assertEqual(self.panel.tree.topLevelItemCount(), 1)
        self.assertIs(self.panel.tree.topLevelItem(0).usage, removable)
        self.assertIn("1개 폴더는 접근 제한", self.panel.status.text())
        self.publish_inventory([protected, empty])
        self.assertEqual(self.panel.tree.topLevelItemCount(), 0)
        self.assertFalse(self.panel.select_all_button.isEnabled())
        self.assertFalse(self.panel.delete_button.isEnabled())
        self.assertIn("정리 가능한 폴더가 없습니다.", self.panel.status.text())

    def test_default_descending_size_and_clickable_numeric_column_sorting(self):
        usages = [self.usage("a", "Alpha", 2048, 2048, 2),
                  self.usage("b", "Zulu", 900, 900, 10),
                  self.usage("c", "Middle", 1024 * 1024, 1024, 1)]
        self.publish_inventory(usages)
        self.show_management()
        tree = self.panel.tree
        header = tree.header()
        self.assertEqual(tree.sortColumn(), 2)
        self.assertEqual(header.sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
        self.assertEqual([tree.topLevelItem(i).usage.removable_bytes for i in range(3)], [2048, 1024, 900])
        keys = (lambda usage: usage.group.label.casefold(), lambda usage: usage.bytes,
                lambda usage: usage.removable_bytes, lambda usage: usage.files)
        for column, key in enumerate(keys):
            with self.subTest(column=column):
                tree.sortItems(column, Qt.SortOrder.DescendingOrder)
                point = QPoint(header.sectionViewportPosition(column) + header.sectionSize(column) // 2,
                               header.height() // 2)
                fixtures.QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=point)
                self.assertEqual(header.sortIndicatorOrder(), Qt.SortOrder.AscendingOrder)
                self.assertEqual([tree.topLevelItem(i).usage for i in range(3)], sorted(usages, key=key))
                fixtures.QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=point)
                self.assertEqual(header.sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
                self.assertEqual([tree.topLevelItem(i).usage for i in range(3)], sorted(usages, key=key, reverse=True))

    def test_select_all_toggle_and_execution_guard(self):
        self.assertFalse(self.panel.select_all_button.isEnabled())
        self.publish_inventory([self.usage("a", "A", 2, 2), self.usage("b", "B", 3, 3)])
        self.panel.select_all_button.click()
        self.assertEqual(len(self.panel._selected()), 2)
        self.assertEqual(self.panel.select_all_button.text(), "모두 해제")
        self.assertTrue(self.panel.delete_button.isEnabled())
        self.panel.set_allowed(False)
        self.assertFalse(self.panel.select_all_button.isEnabled())
        self.panel._toggle_select_all()
        self.assertEqual(len(self.panel._selected()), 2)
        self.panel.set_allowed(True)
        self.panel.select_all_button.click()
        self.assertEqual(self.panel._selected(), [])
        self.assertEqual(self.panel.select_all_button.text(), "모두 선택")
        self.assertFalse(self.panel.delete_button.isEnabled())

    def test_refresh_preserves_sort_direction_and_clears_selections(self):
        usages = [self.usage("a", "A", 20, 20), self.usage("b", "B", 30, 30)]
        self.publish_inventory(usages)
        self.panel.tree.sortItems(1, Qt.SortOrder.AscendingOrder)
        self.panel.select_all_button.click()
        self.publish_inventory(usages)
        self.assertEqual(self.panel.tree.sortColumn(), 1)
        self.assertEqual(self.panel.tree.header().sortIndicatorOrder(), Qt.SortOrder.AscendingOrder)
        self.assertEqual(self.panel._selected(), [])
        self.assertEqual(self.panel.select_all_button.text(), "모두 선택")

    def test_headers_stay_plain_without_sorting_guidance_after_click_and_scan(self):
        usages = [self.usage("a", "A", 20, 20), self.usage("b", "B", 30, 30)]
        self.publish_inventory(usages)
        self.show_management()
        tree = self.panel.tree
        header = tree.header()
        self.assertFalse(header.isSortIndicatorShown())
        self.assertEqual([tree.headerItem().text(i) for i in range(4)], list(self.panel.COLUMN_LABELS))
        point = QPoint(header.sectionViewportPosition(2) + header.sectionSize(2) // 2, header.height() // 2)
        fixtures.QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(header.sortIndicatorOrder(), Qt.SortOrder.AscendingOrder)
        self.assertEqual([tree.headerItem().text(i) for i in range(4)], list(self.panel.COLUMN_LABELS))
        self.assertTrue(all(not tree.headerItem().toolTip(i) for i in range(4)))
        self.publish_inventory(usages)
        self.assertFalse(header.isSortIndicatorShown())
        self.assertEqual([tree.headerItem().text(i) for i in range(4)], list(self.panel.COLUMN_LABELS))
        self.assertTrue(all(not any(symbol in tree.headerItem().text(column) for symbol in "▲▼<>⌃⌄")
                            for column in range(tree.columnCount())))

    def test_rows_have_no_selection_highlight_but_checkboxes_still_work(self):
        self.publish_inventory([self.usage("a", "A", 20, 20)])
        self.show_management()
        tree = self.panel.tree
        settings = self.window.settings_panel
        self.assertEqual(tree.selectionMode(), QAbstractItemView.SelectionMode.NoSelection)
        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                settings.theme_combo.setCurrentIndex(settings.theme_combo.findData(theme))
                settings.navigation.setCurrentRow(settings.section_order().index("management"))
                fixtures.QTest.qWait(20)
                item = tree.topLevelItem(0)
                rect = tree.visualItemRect(item)
                point = QPoint(200, rect.center().y())
                fixtures.QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
                self.assertEqual(tree.selectedItems(), [])
                image = tree.viewport().grab().toImage()
                self.assertEqual(image.pixelColor(point).name(), "#142035" if theme == "dark" else "#f8fafc")
                previous = item.checkState(0)
                fixtures.QTest.keyClick(tree, Qt.Key.Key_Space)
                self.assertNotEqual(item.checkState(0), previous)
                self.assertEqual(tree.selectedItems(), [])

    def test_scrollbar_is_rounded_and_only_spans_the_body_in_both_themes(self):
        self.publish_inventory([self.usage(str(i), f"Log {i}", 100 + i, 100 + i) for i in range(30)])
        self.show_management()
        settings = self.window.settings_panel
        tree = self.panel.tree
        bar = tree.verticalScrollBar()
        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                settings.theme_combo.setCurrentIndex(settings.theme_combo.findData(theme))
                settings.navigation.setCurrentRow(settings.section_order().index("management"))
                fixtures.QTest.qWait(20)
                self.assertGreater(bar.maximum(), 0)
                self.assertEqual(bar.mapTo(tree, QPoint()).y(), tree.viewport().geometry().top())
                self.assertEqual(bar.mapTo(tree, QPoint(0, bar.height())).y(), tree.viewport().geometry().bottom() + 1)
                header_geometry = tree.header().geometry()
                outer_value = settings.detail_scroll.verticalScrollBar().value()
                bar.setValue(bar.maximum() // 2)
                self.assertEqual(tree.header().geometry(), header_geometry)
                self.assertEqual(settings.detail_scroll.verticalScrollBar().value(), outer_value)
                paint_filter = bar._rounded_paint_filter
                surface = paint_filter._surface_color()
                self.assertEqual(surface.name(), "#142035" if theme == "dark" else "#f8fafc")
                image = bar.grab().toImage()
                slider = paint_filter._slider_rect()
                corner = image.pixelColor(slider.left(), slider.top())
                handle = paint_filter._handle_color()
                # Antialiasing blends the rounded cap into the background;
                # it must not be the solid handle-colored square corner.
                self.assertNotEqual(corner, handle)
                distance = lambda first, second: sum(abs(a - b) for a, b in zip(first.getRgb()[:3], second.getRgb()[:3]))
                self.assertLess(distance(corner, surface), distance(corner, handle))
                self.assertEqual(image.pixelColor(slider.center()), paint_filter._handle_color())
                self.assertEqual(tree.horizontalScrollBar().maximum(), 0)
                self.assertEqual(settings.detail_scroll.horizontalScrollBar().maximum(), 0)

    def test_scrollbar_tracks_header_height_changes(self):
        self.publish_inventory([self.usage(str(i), f"Log {i}", 100 + i, 100 + i) for i in range(30)])
        self.show_management()
        tree = self.panel.tree
        tree.header().setFixedHeight(tree.header().height() + 10)
        fixtures.QTest.qWait(20)
        self.assertEqual(tree.verticalScrollBar().mapTo(tree, QPoint()).y(), tree.viewport().geometry().top())

    def test_reset_is_a_separate_setting_row_and_notice_is_project_neutral(self):
        row = self.panel.reset_row
        self.assertEqual(row.objectName(), "settingsRow")
        self.assertEqual(row.title_label.text(), "모든 설정 초기화")
        self.assertIn("초기화 후 앱이 종료", row.description_label.text())
        self.assertIs(row.layout().itemAtPosition(0, 1).widget(), self.panel.reset_button)
        self.assertIs(row.layout().itemAtPosition(1, 1).widget(), self.panel.reset_button)
        self.assertEqual(row.layout().contentsMargins().top(), 12)
        self.assertEqual(row.layout().contentsMargins().bottom(), 0)
        self.assertFalse(any("계정 백업" in label.text() for label in self.panel.findChildren(fixtures.QLabel)))

    def test_reset_cancel_does_not_change_config(self):
        path = self.user / "config/user_config.json"
        before = path.read_bytes()
        with patch("app.winUI.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.window._reset_all_settings()
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.window._reset_settings_pending)

    def test_reset_closes_without_overwriting_new_defaults(self):
        self.window.settings_panel.runtime_edit_checkbox.setChecked(True)
        with patch("app.winUI.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.window._reset_all_settings()
        self.assertTrue(self.window._reset_settings_pending)
        self.window.save_user_config()
        self.assertEqual(json.loads((self.user / "config/user_config.json").read_text()), {})
        config = json.loads((self.user / "config/maa_config.json").read_text())
        self.assertFalse(config["general"]["allow_option_edits_while_running"])

    def test_reset_during_run_is_blocked_before_confirmation(self):
        self.window.isRunning = True
        with patch("app.winUI.QMessageBox.question") as question:
            self.window._reset_all_settings()
        question.assert_not_called()
        self.assertFalse(self.window._reset_settings_pending)
        self.window.isRunning = False


if __name__ == "__main__":
    unittest.main()
