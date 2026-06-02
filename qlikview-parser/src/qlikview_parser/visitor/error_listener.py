"""ANTLR error listener that buffers diagnostics instead of raising."""
from __future__ import annotations

from dataclasses import dataclass

from antlr4.error.ErrorListener import ErrorListener


@dataclass
class ParseError:
    line: int
    column: int
    message: str
    source: str  # "lexer" or "parser"


class CollectingErrorListener(ErrorListener):
    """Buffer lexer/parser diagnostics with line/column for later inspection."""

    def __init__(self, source: str = "parser") -> None:
        super().__init__()
        self.source = source
        self.errors: list[ParseError] = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802
        self.errors.append(
            ParseError(line=line, column=column, message=msg, source=self.source)
        )
