"""Path formatting and actual Qt line layout, with all controller calls mocked."""
import unittest

import test_runtime_lifecycle as fixtures
from PySide6.QtGui import QTextOption
from app.logPresentation import format_log_message


PATH = r"C:\Users\RTE\Desktop\Apps With Spaces\assets\resources\very_long_resource_bundle_name"


class LogPathPresentationTests(unittest.TestCase):
    def test_resource_failure_moves_the_entire_drive_path_to_its_own_line(self):
        self.assertEqual(format_log_message("리소스 로드에 실패했습니다: " + PATH),
                         "리소스 로드에 실패했습니다:\n" + PATH)

    def test_quoted_exception_paths_keep_the_original_quotes_and_separators(self):
        self.assertEqual(format_log_message("파일 확인 실패: '" + PATH + "'"),
                         "파일 확인 실패:\n'" + PATH + "'")

    def test_unc_paths_and_forward_slash_drives_are_supported(self):
        for path in (r"\\server\share\Folder With Spaces\resource.json", "//server/share/folder/file.json", "D:/Apps/Resources"):
            with self.subTest(path=path):
                self.assertEqual(format_log_message("확인 실패: " + path), "확인 실패:\n" + path)

    def test_multiple_paths_are_independent_and_reformatting_is_idempotent(self):
        message = "원본: C:/Source/a.json, 대상: D:/Target/b.json"
        actual = format_log_message(message)
        self.assertEqual(actual, "원본:\nC:/Source/a.json, 대상:\nD:/Target/b.json")
        self.assertEqual(format_log_message(actual), actual)

    def test_urls_relative_paths_and_plain_text_are_not_rewritten(self):
        for text in ("참조: https://github.com/D-Roote/MFWSide", "접속: http://localhost:8080/api/file", "상대 경로: assets/resources",
                     "진행: A 작업", "첫째\n둘째\n", "Windows C 드라이브"):
            self.assertEqual(format_log_message(text), text)

    def test_diagnostic_hiding_remains_unchanged_including_path_on_next_line(self):
        text = "Agent 연결 실패. 진단 로그: " + PATH
        self.assertEqual(format_log_message(text), "Agent 연결 실패.")
        expected = "Agent 연결 실패.\n진단 로그:\n" + PATH.replace("\\", "/")
        self.assertEqual(format_log_message(text, diagnostics=True), expected)
        self.assertEqual(format_log_message(expected), "Agent 연결 실패.")

    def test_already_separated_or_path_only_messages_do_not_gain_empty_lines(self):
        for text in (PATH, "리소스 로드 실패:\n" + PATH, "  " + PATH):
            self.assertEqual(format_log_message(text), text)


class LogPathLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.view = self.window.ui.logPrintText
        self.window.show()
        fixtures.QApplication.processEvents()

    def visual_lines(self):
        fixtures.QApplication.processEvents()
        self.view.document().documentLayout().documentSize()
        block = self.view.document().begin()
        lines = []
        while block.isValid():
            layout = block.layout()
            for index in range(layout.lineCount()):
                line = layout.lineAt(index)
                lines.append(block.text()[line.textStart():line.textStart() + line.textLength()])
            block = block.next()
        return lines

    def test_log_view_uses_character_wrap_instead_of_breaking_at_drive_colons(self):
        self.assertEqual(self.view.wordWrapMode(), QTextOption.WrapMode.WrapAnywhere)

    def test_long_path_visual_lines_never_leave_just_a_drive_prefix(self):
        for width in (280, 320, 480):
            for path in (PATH, "C:/" + "VeryLongResourceComponent" * 30 + "/file.json"):
                with self.subTest(width=width, path=path[:20]):
                    self.view.setFixedWidth(width)
                    self.window.clear_log()
                    self.window.append_log("리소스 로드에 실패했습니다: " + path)
                    lines = self.visual_lines()
                    self.assertFalse(any(line.strip() in ("C", "C:", "C:/", "C:\\") for line in lines), lines)
                    self.assertTrue(any(line.startswith("C:") and len(line) > 4 for line in lines), lines)
                    self.assertEqual(self.view.horizontalScrollBar().maximum(), 0)

    def test_copy_preserves_the_full_original_path_and_next_log_has_no_blank_line(self):
        self.window.clear_log()
        self.window.append_log("리소스 로드에 실패했습니다: " + PATH)
        self.window.append_log("다른 인스턴스는 계속 실행합니다.")
        text = self.view.toPlainText()
        self.assertEqual(text.count(PATH), 1)
        self.assertNotIn("\n\n", text)
        self.window.copy_log()
        self.assertEqual(self.fixture.app.clipboard().text(), text)


if __name__ == "__main__":
    unittest.main()
