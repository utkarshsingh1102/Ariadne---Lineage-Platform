"""spark-submit shell-script parser — v0.2 §7.

Scans a shell script (or any text file) for ``spark-submit`` invocations,
extracts the entry-point script (``.py`` or ``.jar``), the ``--conf`` flags,
``--py-files``, ``--master``, ``--deploy-mode``, ``--name``, and the
positional argv that the entry-point receives. The positional argv is the
hook that lets the cross-file resolver later bind dynamic table-name vars
(``main.py: env = sys.argv[1]``) to the literal passed on the command line.

This is a static text parser — it does not execute the script.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

from ..models.domain import (
    OrchestrationJobIR,
    OrchestrationTaskIR,
    WarningIR,
)


_SPARK_SUBMIT_RE = re.compile(r"\bspark-submit\b")


def parse_spark_submit(file_path: str | Path) -> OrchestrationJobIR:
    p = Path(file_path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        job = OrchestrationJobIR(
            job_id=p.stem, source="spark_submit", file_path=str(p),
        )
        job.warnings.append(WarningIR(
            type="spark_submit_read_error", detail=str(e),
        ))
        return job

    job = OrchestrationJobIR(
        job_id=p.stem, source="spark_submit", file_path=str(p),
    )

    # Join shell line continuations (`foo \\\n   bar`) so each spark-submit
    # invocation sits on one logical line.
    joined = _join_shell_continuations(text)

    for raw_line in joined.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not _SPARK_SUBMIT_RE.search(line):
            continue
        task = _parse_invocation(line)
        if task is not None:
            job.tasks.append(task)

    return job


def _join_shell_continuations(text: str) -> str:
    """Replace ``\\<newline>`` sequences with a single space.

    Preserves all other whitespace; safe for both shebang and comment lines
    (we never call this on lines that begin with ``#``).
    """
    out: list[str] = []
    pending: list[str] = []
    for line in text.split("\n"):
        if line.endswith("\\"):
            pending.append(line[:-1].rstrip())
            continue
        if pending:
            pending.append(line.strip())
            out.append(" ".join(pending))
            pending = []
        else:
            out.append(line)
    if pending:
        out.append(" ".join(pending))
    return "\n".join(out)


def _parse_invocation(line: str) -> OrchestrationTaskIR | None:
    """Parse one ``spark-submit ...`` command line.

    Honours shell line continuations only when the caller has already joined
    them. Best-effort: malformed quoting falls through silently.
    """
    try:
        tokens = shlex.split(line, comments=True, posix=True)
    except ValueError:
        return None
    if "spark-submit" not in tokens:
        return None

    # Drop everything before `spark-submit` (handles things like `exec spark-submit …`).
    idx = tokens.index("spark-submit")
    tokens = tokens[idx + 1:]

    params: dict[str, str] = {}
    application: str | None = None
    app_args: list[str] = []

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        # Long options that take a value: `--master local`, `--conf k=v`, etc.
        if tok.startswith("--"):
            value: str | None = None
            if "=" in tok:
                opt, value = tok[2:].split("=", 1)
            else:
                opt = tok[2:]
                if i + 1 < len(tokens):
                    value = tokens[i + 1]
                    i += 1
            if opt == "conf" and value and "=" in value:
                k, v = value.split("=", 1)
                params[f"conf.{k}"] = v
            elif value is not None:
                params[opt] = value
            i += 1
            continue
        # The first non-flag token is the entry-point application.
        application = tok
        app_args = tokens[i + 1:]
        break

    if application is None:
        return None
    if app_args:
        params["argv"] = " ".join(app_args)
        for n, arg in enumerate(app_args):
            params[f"argv[{n}]"] = arg

    return OrchestrationTaskIR(
        task_id=Path(application).stem,
        operator="spark_submit",
        target_script=application,
        parameters=params,
    )
