"""Bind SDK device names to unique desktop titles; never infer an ADB port map."""
from copy import deepcopy
import os
import re
import unicodedata


def window_identity(window):
    if not isinstance(window, dict):
        return None
    hwnd, pid = window.get("hwnd"), window.get("pid")
    return (hwnd, pid) if type(hwnd) is int and hwnd > 0 and type(pid) is int and pid > 0 else None


def _target_data(target):
    if isinstance(target, dict):
        return target
    device = getattr(target, "device", None)
    return {"key": getattr(target, "key", ""), "label": getattr(target, "label", ""),
            "kind": getattr(target, "kind", ""),
            "device": {"name": getattr(device, "name", ""), "config": getattr(device, "config", {})}}


def _normalize(value):
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    value = value.translate(str.maketrans({"–": "-", "—": "-", "−": "-"}))
    return re.sub(r"\s*-\s*", "-", " ".join(value.split()))


def device_name(target):
    target = _target_data(target)
    device = target.get("device") or {}
    name = device.get("name")
    if not isinstance(name, str) or not name.strip():
        name = target.get("label", "")
        if not isinstance(name, str):
            return ""
        name = name.removesuffix(" · " + str(target.get("key", "")))
    key, name = _normalize(str(target.get("key", ""))), _normalize(name)
    # Generic SDK fallback names are serial-ADB-path, not instance titles.
    return "" if not name or name == key or (key and name.startswith(key + "-")) else name


def _name_variants(target):
    target = _target_data(target)
    name = device_name(target)
    if not name:
        return set()
    names = {name}
    config = (target.get("device") or {}).get("config") or {}
    # v5.12.3 named EmulatorExtras devices use instance-name + '-' + SDK engine
    # name. Only remove that suffix for SDK extras metadata, not arbitrary titles.
    if isinstance(config, dict) and isinstance(config.get("extras"), dict) and config["extras"] and "-" in name:
        title, engine = name.rsplit("-", 1)
        if title and engine:
            names.add(title)
            engine = re.sub(r"\s+v?\d[\d.+-]*$", "", engine)
            names.add(title + "-" + engine)
    return names


def match_windows(targets, windows, *, reserved=(), excluded_pids=()):
    """Return key->window and ambiguous keys; collisions never choose a winner."""
    targets = [_target_data(target) for target in targets]
    targets = {target["key"]: target for target in targets
               if isinstance(target.get("key"), str) and target["key"].strip()}
    reserved_ids = {window_identity(window) for window in reserved}
    excluded = {os.getpid(), *excluded_pids}
    matches, ambiguous = {}, set()
    claims = {}
    for key, target in targets.items():
        if target.get("kind") != "Adb":
            continue
        names = _name_variants(target)
        candidates = {}
        for window in windows:
            identity = window_identity(window)
            title = window.get("label", "") if isinstance(window, dict) else ""
            if (identity is None or identity in reserved_ids or identity[1] in excluded
                    or not isinstance(title, str) or _normalize(title) not in names):
                continue
            candidates[identity] = window
        if len(candidates) > 1:
            ambiguous.add(key)
        elif candidates:
            identity, window = next(iter(candidates.items()))
            matches[key] = {**deepcopy(window), "automatic": True}
            claims.setdefault(identity, []).append(key)
    for keys in claims.values():
        if len(keys) > 1:
            ambiguous.update(keys)
    return {key: window for key, window in matches.items() if key not in ambiguous}, ambiguous


def desktop_windows():
    from maa.toolkit import Toolkit
    from app.monitoring import window_process_info
    windows = []
    for window in Toolkit.find_desktop_windows() or []:
        if not window.window_name:
            continue
        hwnd = getattr(window.hwnd, "value", window.hwnd)
        pid, path = window_process_info(hwnd)
        if pid and pid != os.getpid():
            windows.append({"hwnd": hwnd, "pid": pid, "path": path, "label": window.window_name})
    return windows


def bind_runtime_window(runtime):
    """Bind only identity metadata. This function never minimizes or activates."""
    connection = getattr(runtime, "connection", None)
    if not isinstance(connection, dict):
        connection = getattr(runtime, "_adb_execution_connection", {})
    explicit = connection.get("emulator") or {}
    if explicit and not explicit.get("automatic"):
        return explicit
    target = connection.get("target") or {}
    if not device_name(target) or target.get("_window_ambiguous"):
        # An automatic choice from a previous address/name is not an override.
        connection["emulator"] = {}
        return {}
    snapshot = getattr(runtime, "snapshot", {})
    matches, ambiguous = match_windows(
        [target], desktop_windows(), reserved=snapshot.get("reserved_windows", ()),
        excluded_pids=(snapshot.get("client_pid"),),
    )
    window = matches.get(target.get("key"), {})
    connection["emulator"] = window
    if target.get("key") in ambiguous:
        target["_window_ambiguous"] = True
    return window
