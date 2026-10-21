import sys
from multiprocessing import freeze_support


def _schedule_exit_cleanup(window):
    if not (window.settings_panel.auto_cleanup_on_exit_enabled()
            or getattr(window, "_reset_settings_pending", False) is True):
        return
    from app.exitCleanup import schedule_exit_cleanup
    try:
        schedule_exit_cleanup()
    except OSError as error:
        from app.afterActions import write_after_action_log
        try:
            write_after_action_log(window.runtime.user_dir / "debug" / "cleanup_error.log",
                                   f"종료 후 자동 로그 정리를 시작하지 못했습니다: {error}")
        except OSError:
            pass


def main() -> int:
    if sys.argv[1:2] == ["--cleanup-after-exit"]:
        sys.dont_write_bytecode = True
        from app.exitCleanup import run_cleanup_after_exit_cli
        return run_cleanup_after_exit_cli(sys.argv[1:])
    if sys.argv[1:2] == ["--after-exit"]:
        from app.afterActions import run_after_exit_cli
        return run_after_exit_cli(sys.argv[1:])
    from app.paths import PATHS
    from app.crashDiagnostics import enable_fault_log
    enable_fault_log(PATHS.user_dir / "debug")
    # Spawn workers import __main__; they must not import/create the GUI.
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import QTimer
    from app.winUI import MainWindow
    app = QApplication(sys.argv)

    try:
        window = MainWindow()
    except (OSError, ValueError, TypeError, KeyError) as error:
        QMessageBox.critical(None, "앱 초기화 오류", f"프로젝트 설정을 확인하세요.\n{error}")
        return 1
    window.show()
    app.setApplicationDisplayName(window.windowTitle())
    QTimer.singleShot(0, window.app_startup.start)
    
    result = app.exec()
    _schedule_exit_cleanup(window)
    return result


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
