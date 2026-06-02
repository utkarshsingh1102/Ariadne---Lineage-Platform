"""Composer-text path: file → list[ScheduleIR] via ANTLR."""

from __future__ import annotations

from pathlib import Path

from antlr4 import CommonTokenStream, InputStream

from tws_parser.generated.TWSComposerLexer import TWSComposerLexer
from tws_parser.generated.TWSComposerParser import TWSComposerParser
from tws_parser.models.domain import ParsedComposerUnit, ScheduleIR
from tws_parser.visitor.error_listener import CollectedError, CollectingErrorListener
from tws_parser.visitor.ir_visitor import TWSIRVisitor


def parse_composer_text(path_or_text: str) -> list[ScheduleIR]:
    """Convenience: returns schedules only, drops the error list.

    Use ``parse_composer_text_with_errors`` if you need the lexer/parser
    diagnostics (the API path does — silently dropping them is the bug that
    motivated v0.2).
    """
    schedules, _ = parse_composer_text_with_errors(path_or_text)
    return schedules


def parse_composer_text_with_errors(
    path_or_text: str,
) -> tuple[list[ScheduleIR], list[CollectedError]]:
    """Parse a composer-text source and return BOTH the IR and collected errors.

    Accepts either a file path or the raw text body (a string with newlines).
    The errors list contains every ANTLR lexer + parser diagnostic — empty
    list means a clean parse. Callers MUST inspect this list before treating
    a zero-schedule result as legitimate.
    """
    unit, errors = parse_composer_full_with_errors(path_or_text)
    return unit.schedules, errors


def parse_composer_full_with_errors(
    path_or_text: str,
) -> tuple[ParsedComposerUnit, list[CollectedError]]:
    """v0.2 — return the full topology IR (schedules, job streams, workstations,
    calendars, resources, prompts, event rules) plus the collected errors.

    The v0.2 API + Neo4j writer use this; v0.1 callers stick with
    ``parse_composer_text_with_errors`` which yields only schedules.
    """
    text = _load(path_or_text)
    text = _normalize(text)
    return _parse_text(text)


def _parse_text(text: str) -> tuple[ParsedComposerUnit, list[CollectedError]]:
    stream = InputStream(text)

    lexer = TWSComposerLexer(stream)
    lexer_errors = CollectingErrorListener()
    lexer.removeErrorListeners()
    lexer.addErrorListener(lexer_errors)

    tokens = CommonTokenStream(lexer)
    parser = TWSComposerParser(tokens)
    parser_errors = CollectingErrorListener()
    parser.removeErrorListeners()
    parser.addErrorListener(parser_errors)

    tree = parser.compilationUnit()
    unit = TWSIRVisitor().visit_full(tree) or ParsedComposerUnit()
    errors = list(lexer_errors.errors) + list(parser_errors.errors)
    return unit, errors


def _load(path_or_text: str) -> str:
    # Heuristic: treat as a path only if it's short, single-line, and the
    # filesystem can stat it. Newlines or leading whitespace mean the caller
    # passed raw composer text — don't try ``Path().exists()`` on it (very
    # long single-line text trips macOS's 255-byte component limit).
    if not isinstance(path_or_text, str):
        return path_or_text
    if "\n" in path_or_text or path_or_text.lstrip() != path_or_text:
        return path_or_text
    if len(path_or_text) >= 4096:
        return path_or_text
    try:
        p = Path(path_or_text)
        if p.exists():
            return p.read_text(encoding="utf-8")
    except OSError:
        pass
    return path_or_text


def _normalize(text: str) -> str:
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")
