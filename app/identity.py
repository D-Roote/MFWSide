"""Resource-facing display names and the stable, resource-neutral client identity."""
import re


DEFAULT_APP_NAME = "MFWSide"


def display_name(value):
    return value.strip() if isinstance(value, str) and value.strip() else DEFAULT_APP_NAME


def interface_titles(interface):
    interface = interface if isinstance(interface, dict) else {}
    return display_name(interface.get("label")), display_name(interface.get("title"))


def log_filename_stem(title):
    """Do not let resource text become Windows path components or invalid names."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", display_name(title))
    return name[:80].strip(" .") or DEFAULT_APP_NAME
