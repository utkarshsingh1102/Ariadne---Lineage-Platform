"""
Script resolution (plan §6 step 6).
SCRIPTNAME → script_path + args; script_type inferred from extension.
"""
import pytest


@pytest.mark.parametrize("path,expected_type", [
    ("/apps/abinitio/run.sh extract.mp", "shell"),     # .sh wins over .mp arg
    ("/apps/jobs/load.mp", "abinitio"),
    ("/apps/bteq/load_orders.bteq", "bteq"),
    ("/apps/sql/init.sql", "bteq"),
    ("/apps/scripts/run.ksh", "shell"),
    ("/apps/unknown/blob", "unknown"),
])
def test_script_type_inferred(path, expected_type):
    from tws_parser.parser.script_resolver import infer_script_type
    assert infer_script_type(path) == expected_type


def test_args_split_when_enabled(monkeypatch):
    """SCRIPT_PATH_STRIP_ARGS=true → split at first space."""
    from tws_parser.parser.script_resolver import resolve_script
    monkeypatch.setenv("SCRIPT_PATH_STRIP_ARGS", "true")
    path, args = resolve_script("/apps/abinitio/run.sh extract.mp --debug")
    assert path == "/apps/abinitio/run.sh"
    assert args == "extract.mp --debug"


def test_args_kept_when_disabled(monkeypatch):
    from tws_parser.parser.script_resolver import resolve_script
    monkeypatch.setenv("SCRIPT_PATH_STRIP_ARGS", "false")
    path, args = resolve_script("/apps/abinitio/run.sh extract.mp")
    assert path == "/apps/abinitio/run.sh extract.mp"
    assert args is None


def test_quoted_path_with_space(monkeypatch):
    """A quoted SCRIPTNAME with a space in the path must NOT be split."""
    from tws_parser.parser.script_resolver import resolve_script
    monkeypatch.setenv("SCRIPT_PATH_STRIP_ARGS", "true")
    path, args = resolve_script('"/apps/with space/run.sh" arg1')
    assert path == "/apps/with space/run.sh"
    assert args == "arg1"
