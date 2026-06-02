"""Custom ANTLR ErrorListener that collects (instead of printing)."""

from __future__ import annotations

from dataclasses import dataclass, field

from antlr4.error.ErrorListener import ErrorListener


@dataclass
class CollectedError:
    line: int
    column: int
    msg: str


@dataclass
class CollectingErrorListener(ErrorListener):
    errors: list[CollectedError] = field(default_factory=list)

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802
        self.errors.append(CollectedError(line=line, column=column, msg=msg))

    # The remaining ErrorListener hooks (ambiguity, full-context attempts, …) are
    # intentionally left as no-ops — they're informational, not failures.
