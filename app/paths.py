"""Keep bundled application files separate from external resources/user data."""
from dataclasses import dataclass
from pathlib import Path
import sys


@dataclass(frozen=True)
class ApplicationPaths:
    root_dir: Path
    app_dir: Path

    @property
    def assets_dir(self):
        return self.root_dir / "assets"

    @property
    def resource_dir(self):
        return self.assets_dir / "resources"

    @property
    def user_dir(self):
        return self.assets_dir / "user"

    @property
    def interface_path(self):
        return self.resource_dir / "interface.json"

    @property
    def ui_dir(self):
        return self.app_dir / "pySide6"

    @property
    def ui_resource_dir(self):
        return self.app_dir / "resources"


def resolve_application_paths(app_dir=None, *, frozen=None, executable=None):
    package_dir = Path(app_dir).resolve() if app_dir is not None else Path(__file__).resolve().parent
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    root = Path(executable or sys.executable).resolve().parent if frozen else package_dir.parent
    return ApplicationPaths(root_dir=root, app_dir=package_dir)


PATHS = resolve_application_paths()
