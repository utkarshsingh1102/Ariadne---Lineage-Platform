"""
ANTLR grammar tests (plan §10.1).
Drive the generated lexer/parser directly. Assert parse-tree rule shape and
collected errors via CollectingErrorListener.
"""
import pytest


def _parse(text: str):
    """Helper: lex + parse and return (tree, errors_list).

    The parser is stashed on the tree as `_parser` so callers can do
    `tree.toStringTree(recog=tree._parser)` to get rule **names** instead of
    rule indices (ANTLR's default when no recogniser is provided).
    """
    from antlr4 import InputStream, CommonTokenStream
    from tws_parser.generated.TWSComposerLexer import TWSComposerLexer
    from tws_parser.generated.TWSComposerParser import TWSComposerParser
    from tws_parser.visitor.error_listener import CollectingErrorListener

    stream = InputStream(text)
    lexer = TWSComposerLexer(stream)
    errs = CollectingErrorListener()
    lexer.removeErrorListeners(); lexer.addErrorListener(errs)
    tokens = CommonTokenStream(lexer)
    parser = TWSComposerParser(tokens)
    parser.removeErrorListeners(); parser.addErrorListener(errs)
    tree = parser.compilationUnit()
    tree._parser = parser
    return tree, errs


def test_minimal_schedule_parses(fixture_text):
    tree, errs = _parse(fixture_text("01_single_schedule_single_job.txt"))
    assert tree is not None
    assert errs.errors == []


def test_multi_job_schedule_parses(fixture_text):
    tree, errs = _parse(fixture_text("02_multi_job_with_follows.txt"))
    assert errs.errors == []
    # tree should contain one scheduleDefinition with three jobDefinition children
    s = tree.toStringTree(recog=tree._parser)
    assert s.count("jobDefinition") >= 3


def test_schedule_level_follows_parsed(fixture_text):
    tree, errs = _parse(fixture_text("03_schedule_level_dependency.txt"))
    assert errs.errors == []


def test_resource_and_file_deps_parsed(fixture_text):
    tree, errs = _parse(fixture_text("04_resource_and_file_deps.txt"))
    assert errs.errors == []


def test_realistic_dump_parses_without_errors(fixture_text):
    tree, errs = _parse(fixture_text("06_realistic_dump_many_schedules.txt"))
    assert errs.errors == [], f"Realistic fixture has parse errors: {errs.errors}"


# -----------------------------------------------------------------------------
# Negative tests — plan §10.1: assert ErrorListener catches the expected errors
# -----------------------------------------------------------------------------

def test_missing_end_keyword_is_caught():
    text = "SCHEDULE WS#M#X\nAT 0530\n:\n  J\n    SCRIPTNAME \"/x\"\n    STREAMLOGON u\n"
    _tree, errs = _parse(text)
    assert len(errs.errors) >= 1


def test_unclosed_string_literal_is_caught():
    text = 'SCHEDULE WS#M#X\nAT 0530\n:\n  J\n    SCRIPTNAME "/x\n    STREAMLOGON u\nEND\n'
    _tree, errs = _parse(text)
    assert len(errs.errors) >= 1


def test_invalid_keyword_inside_job_is_caught():
    text = ('SCHEDULE WS#M#X\nAT 0530\n:\n  J\n    SCRIPTNAME "/x"\n'
            '    STREAMLOGON u\n    UNKNOWNKW foo\nEND\n')
    _tree, errs = _parse(text)
    assert len(errs.errors) >= 1


# -----------------------------------------------------------------------------
# Lexer skip rules — comments must not produce tokens
# -----------------------------------------------------------------------------

def test_line_comments_skipped():
    text = "# this is a comment\nSCHEDULE WS#M#X\n:\n  J\n    SCRIPTNAME \"/x\"\n    STREAMLOGON u\nEND\n"
    _tree, errs = _parse(text)
    assert errs.errors == []


def test_block_comments_skipped():
    text = ("/* multi-line\n   block comment */\n"
            "SCHEDULE WS#M#X\n:\n  J\n    SCRIPTNAME \"/x\"\n    STREAMLOGON u\nEND\n")
    _tree, errs = _parse(text)
    assert errs.errors == []
