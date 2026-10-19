"""Versioned, project-neutral task configurations; never store passwords."""
from uuid import uuid4


PRESET_VERSION = 1
MAX_PRESET_NAME = 80


def clean_preset_name(value):
    name = value.strip() if isinstance(value, str) else ""
    if not name or len(name) > MAX_PRESET_NAME or any(character in name for character in "\r\n\0"):
        raise ValueError(f"프리셋 이름은 1~{MAX_PRESET_NAME}자로 입력하세요.")
    return name


def sanitize_tasks(records, interface):
    """Keep the existing task-list format and only declared non-secret options.

    Missing tasks remain identifiable so applying a preset can report them,
    rather than silently replacing the user's current task list with defaults.
    """
    if not isinstance(records, list):
        return []
    definitions = interface.get("option", {})
    definitions = definitions if isinstance(definitions, dict) else {}
    result = []
    for record in records:
        if not isinstance(record, dict):
            continue
        name, entry = record.get("name"), record.get("entry")
        name = name if isinstance(name, str) else ""
        entry = entry if isinstance(entry, str) else ""
        if not name and not entry:
            continue
        options = {}
        raw_options = record.get("selected_options")
        for key, value in (raw_options.items() if isinstance(raw_options, dict) else []):
            definition = definitions.get(key)
            if not isinstance(definition, dict):
                continue
            if definition.get("type", "select") == "input":
                if isinstance(value, dict):
                    options[key] = {field["name"]: value[field["name"]]
                                    for field in definition.get("inputs", [])
                                    if isinstance(field, dict) and isinstance(field.get("name"), str)
                                    and not field.get("password") and isinstance(value.get(field["name"]), str)}
            elif isinstance(value, list):
                selected = [case["name"] for case in definition.get("cases", [])
                            if isinstance(case, dict) and isinstance(case.get("name"), str) and case["name"] in value]
                options[key] = selected if definition.get("type") == "checkbox" else selected[:1]
        result.append({"name": name, "entry": entry, "checked": record.get("checked") is True,
                       "selected_options": options})
    return result


def make_task_preset(name, tasks, interface, preset_id=None):
    name = clean_preset_name(name)
    tasks = sanitize_tasks(tasks, interface)
    if not tasks:
        raise ValueError("저장할 작업이 없습니다.")
    return {"id": preset_id or uuid4().hex, "name": name, "tasks": tasks}


def normalize_task_presets(data, interface):
    if (not isinstance(data, dict) or type(data.get("version")) is not int
            or data["version"] != PRESET_VERSION or not isinstance(data.get("items"), list)):
        return []
    result, seen = [], set()
    for record in data["items"]:
        if not isinstance(record, dict):
            continue
        preset_id = record.get("id")
        if not isinstance(preset_id, str) or not preset_id.strip() or preset_id in seen:
            continue
        try:
            preset = make_task_preset(record.get("name"), record.get("tasks"), interface, preset_id)
        except ValueError:
            continue
        result.append(preset)
        seen.add(preset_id)
    return result
