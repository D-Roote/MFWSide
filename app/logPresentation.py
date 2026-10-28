"""Keep diagnostics private by default and display complete paths on new lines."""
import re


_DIAGNOSTIC_LABEL = re.compile(r"진단[ \t]*로그[ \t]*:[ \t]*")
_WINDOWS_PATH_START = re.compile(
    r'''(?<![\w:/\\])["']?(?:[A-Za-z]:[\\/]|\\\\[^\s\\/]+[\\/][^\s\\/]+|//[^\s/]+/[^\s/]+)'''
)


def _paths_on_new_lines(text):
    lines = []
    for line in text.split("\n"):
        for match in reversed(list(_WINDOWS_PATH_START.finditer(line))):
            position = match.start()
            if line[:position].strip():
                line = line[:position].rstrip() + "\n" + line[position:]
        lines.append(line)
    return "\n".join(lines)


def format_log_message(message, *, diagnostics=False):
    text = str(message)
    if not _DIAGNOSTIC_LABEL.search(text):
        return _paths_on_new_lines(text)
    lines = iter(text.splitlines())
    visible = []
    for line in lines:
        marker = _DIAGNOSTIC_LABEL.search(line)
        if marker is None:
            visible.append(line)
            continue
        prefix = line[:marker.start()].rstrip()
        path = line[marker.end():].strip() or next(lines, "").strip()
        if prefix:
            visible.append(prefix)
        if diagnostics:
            visible.extend(("진단 로그:", path.replace("\\", "/")))
    return _paths_on_new_lines("\n".join(visible).strip("\n"))
