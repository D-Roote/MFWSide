"""Optional window icon shared by the main window and application dialogs."""
from PySide6.QtGui import QIcon

from app.paths import PATHS


def apply_application_icon(application, paths=PATHS):
    path = paths.ui_resource_dir / "appIcon" / "icon.ico"
    if not path.is_file():
        return False
    icon = QIcon(str(path))
    # QIcon may defer decoding; leave the existing default on corrupt files.
    if icon.isNull() or icon.pixmap(32, 32).isNull():
        return False
    application.setWindowIcon(icon)
    return True
