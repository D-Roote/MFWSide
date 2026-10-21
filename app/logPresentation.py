"""Keep diagnostic files available without exposing their paths by default."""
import re


_DIAGNOSTIC_LABEL = re.compile(r"진단[ \t]*로그[ \t]*:[ \t]*")


def format_log_message(message, *, diagnostics=False):
    text = str(message)
    if not _DIAGNOSTIC_LABEL.search(text):
        return text
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
    return "\n".join(visible).strip("\n")
