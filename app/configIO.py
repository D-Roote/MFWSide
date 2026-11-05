"""Replace complete JSON files atomically without sharing temporary filenames."""
import json
import os
from pathlib import Path
import tempfile


def write_json(path, data):
    path = Path(path)
    contents = json.dumps(data, ensure_ascii=False, indent=4)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
