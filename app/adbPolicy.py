"""Keep one controller's reconnect attempts from resetting the shared ADB server."""
from copy import deepcopy


def shared_adb_options(options):
    options = deepcopy(options)
    config = options.setdefault("config", {})
    if not isinstance(config, dict):
        raise ValueError("ADB 컨트롤러 config는 객체여야 합니다.")
    command = config.setdefault("command", {})
    if not isinstance(command, dict):
        raise ValueError("ADB 컨트롤러 command는 객체여야 합니다.")
    # MaaFW 5.12.3 retries screencap by invoking command.KillServer. Its
    # default `adb kill-server` disconnects EVERY session. A client-only
    # version query is a portable no-op; an empty argv is rejected by SDK.
    command["KillServer"] = ["{ADB}", "version"]
    return options
