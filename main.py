import sys
from multiprocessing import freeze_support

def main() -> int:
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
    
    return app.exec()


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
