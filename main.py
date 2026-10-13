import sys
from multiprocessing import freeze_support

def main() -> int:
    from pathlib import Path
    from app.crashDiagnostics import enable_fault_log
    enable_fault_log(Path(__file__).resolve().parent / "user" / "debug")
    # Spawn workers import __main__; they must not import/create the GUI.
    from PySide6.QtWidgets import QApplication
    from app.winUI import MainWindow
    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()
    
    return app.exec()


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
