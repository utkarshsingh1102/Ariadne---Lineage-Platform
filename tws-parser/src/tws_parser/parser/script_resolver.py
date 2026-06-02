"""Classify a TWS SCRIPTNAME string."""

from __future__ import annotations

import os
import re
import shlex

_TYPE_BY_EXT = {
    ".sh": "shell",
    ".ksh": "shell",
    ".bash": "shell",
    ".mp": "abinitio",
    ".graph": "abinitio",
    ".bteq": "bteq",
    ".sql": "bteq",   # tests treat .sql as bteq (the operational queries)
    ".py": "python",
}


def infer_script_type(path: str) -> str:
    """Look at the *first* path component's extension. `run.sh extract.mp` → shell."""
    if not path:
        return "unknown"
    first = path.split()[0] if " " in path else path
    ext = os.path.splitext(first)[1].lower()
    return _TYPE_BY_EXT.get(ext, "unknown")


_QUOTED_HEAD_RE = re.compile(r'^"([^"]*)"\s*(.*)$')


def resolve_script(raw: str) -> tuple[str, str | None]:
    """`SCRIPTNAME` body → `(script_path, args_or_None)`.

    Honours the live env var `SCRIPT_PATH_STRIP_ARGS` so tests can flip the
    behaviour with `monkeypatch.setenv`:
      * `true`  → split at the first space (after a quoted-path strip)
      * `false` → keep the whole string as the path, args = None
    """
    if raw is None:
        return ("", None)
    s = raw.strip()
    if not s:
        return ("", None)

    strip = os.environ.get("SCRIPT_PATH_STRIP_ARGS", "true").lower() != "false"

    # Quoted-path form (`"/apps/with space/run.sh" arg1`) — always honour the quotes
    m = _QUOTED_HEAD_RE.match(s)
    if m:
        path = m.group(1)
        rest = m.group(2).strip()
        if strip:
            return (path, rest or None)
        # Even when arg-stripping is disabled we keep the path unquoted for cleanliness;
        # any args are stitched back on.
        return ((path + " " + rest).strip() if rest else path, None)

    # Bare form
    if " " in s and strip:
        path, _, args = s.partition(" ")
        return (path, args.strip() or None)
    return (s, None)
