"""Phase 2 unit tests — pre-processor control-flow unrolling."""
from __future__ import annotations

import tempfile

from qlikview_parser.control_flow import unroll
from qlikview_parser.preprocessor import preprocess


def _unroll(script: str, variables: dict[str, str] | None = None) -> tuple[str, list]:
    """Convenience: call unroll() directly without going through preprocess()."""
    diags: list = []
    return unroll(script, variables or {}, diags, artifact="<test>"), diags


# ---------------------------------------------------------------------------
# Static FOR i = a TO b
# ---------------------------------------------------------------------------


def test_static_for_loop_unrolls_three_iterations():
    script = (
        "FOR i = 1 TO 3\n"
        "  LOAD * FROM 'sales_$(i).qvd';\n"
        "NEXT i\n"
    )
    out, diags = _unroll(script)
    # The unroller substitutes $(i) INLINE per iteration so each body is
    # concrete (no SET emission — that would collapse to one value under
    # the single-shot macro pass).
    assert out.count("LOAD * FROM 'sales_1.qvd';") == 1
    assert out.count("LOAD * FROM 'sales_2.qvd';") == 1
    assert out.count("LOAD * FROM 'sales_3.qvd';") == 1
    assert "$(i)" not in out
    assert diags == []


def test_static_for_loop_with_step():
    out, diags = _unroll("FOR i = 0 TO 10 STEP 5\n  TRACE $(i);\nNEXT i\n")
    assert "TRACE 0;" in out
    assert "TRACE 5;" in out
    assert "TRACE 10;" in out
    assert "TRACE 15;" not in out
    assert diags == []


def test_for_loop_with_variable_bound_uses_resolved_value():
    out, diags = _unroll(
        "FOR i = 1 TO $(vCount)\n  TRACE $(i);\nNEXT i\n",
        variables={"vCount": "2"},
    )
    assert "TRACE 1;" in out
    assert "TRACE 2;" in out
    assert "TRACE 3;" not in out
    assert diags == []


def test_for_loop_with_unresolved_bound_emits_diagnostic():
    out, diags = _unroll(
        "FOR i = 1 TO $(vUnknown)\n  TRACE $(i);\nNEXT i\n",
    )
    # Block left intact — original FOR/NEXT preserved
    assert "FOR i = 1 TO $(vUnknown)" in out
    assert any(d.code == "QV-FOR-DYNAMIC" for d in diags)


def test_for_loop_explosion_guard():
    out, diags = _unroll("FOR i = 1 TO 5000\n  TRACE $(i);\nNEXT i\n")
    # Should NOT unroll 5000 iterations — emit guard diagnostic instead.
    assert "FOR i = 1 TO 5000" in out
    assert any(d.code == "QV-FOR-EXPLOSION" for d in diags)


# ---------------------------------------------------------------------------
# FOR EACH x IN ...
# ---------------------------------------------------------------------------


def test_foreach_static_string_list():
    out, diags = _unroll(
        "FOR EACH region IN 'EMEA', 'NA', 'APAC'\n"
        "  LOAD * FROM '$(region)_sales.qvd';\n"
        "NEXT region\n"
    )
    # Inline substitution: $(region) is replaced per iteration with the
    # unquoted item value, so we end up with three concrete LOAD lines.
    assert "LOAD * FROM 'EMEA_sales.qvd';" in out
    assert "LOAD * FROM 'NA_sales.qvd';" in out
    assert "LOAD * FROM 'APAC_sales.qvd';" in out
    assert "$(region)" not in out
    assert diags == []


def test_foreach_filelist_emits_dynamic_diagnostic():
    out, diags = _unroll(
        "FOR EACH file IN filelist('data/*.csv')\n"
        "  LOAD * FROM $(file);\n"
        "NEXT file\n"
    )
    # filelist() is dynamic — block left intact.
    assert "filelist" in out
    assert any(d.code == "QV-FOREACH-DYNAMIC" for d in diags)


# ---------------------------------------------------------------------------
# IF / ELSEIF / ELSE / ENDIF
# ---------------------------------------------------------------------------


def test_if_static_true_keeps_then_branch_drops_else():
    out, diags = _unroll(
        "IF $(vEnv) = 'PROD' THEN\n"
        "  SQL SELECT * FROM PROD.sales;\n"
        "ELSE\n"
        "  SQL SELECT * FROM DEV.sales;\n"
        "ENDIF\n",
        variables={"vEnv": "'PROD'"},
    )
    assert "PROD.sales" in out
    assert "DEV.sales" not in out
    assert diags == []


def test_if_static_false_keeps_else_branch():
    out, diags = _unroll(
        "IF $(vEnv) = 'PROD' THEN\n"
        "  SQL SELECT * FROM PROD.sales;\n"
        "ELSE\n"
        "  SQL SELECT * FROM DEV.sales;\n"
        "ENDIF\n",
        variables={"vEnv": "'DEV'"},
    )
    assert "DEV.sales" in out
    assert "PROD.sales" not in out
    assert diags == []


def test_if_elseif_chain_picks_matching_branch():
    out, diags = _unroll(
        "IF $(vEnv) = 'PROD' THEN\n"
        "  TRACE prod;\n"
        "ELSEIF $(vEnv) = 'STAGING' THEN\n"
        "  TRACE staging;\n"
        "ELSE\n"
        "  TRACE dev;\n"
        "ENDIF\n",
        variables={"vEnv": "'STAGING'"},
    )
    assert "TRACE staging;" in out
    assert "TRACE prod;" not in out
    assert "TRACE dev;" not in out
    assert diags == []


def test_if_dynamic_predicate_leaves_block_intact():
    out, diags = _unroll(
        "IF $(vUnknown) = 'PROD' THEN\n"
        "  TRACE prod;\n"
        "ENDIF\n",
    )
    assert "IF $(vUnknown) = 'PROD'" in out
    assert any(d.code == "QV-IF-DYNAMIC" for d in diags)


# ---------------------------------------------------------------------------
# Nesting
# ---------------------------------------------------------------------------


def test_for_inside_if_static_both():
    out, diags = _unroll(
        "IF $(vEnv) = 'PROD' THEN\n"
        "  FOR i = 1 TO 2\n"
        "    TRACE $(i);\n"
        "  NEXT i\n"
        "ENDIF\n",
        variables={"vEnv": "'PROD'"},
    )
    assert "TRACE 1;" in out
    assert "TRACE 2;" in out
    assert diags == []


def test_nested_for_loops_both_unroll():
    out, diags = _unroll(
        "FOR i = 1 TO 2\n"
        "  FOR j = 1 TO 2\n"
        "    TRACE $(i)-$(j);\n"
        "  NEXT j\n"
        "NEXT i\n"
    )
    # Inline substitution per iteration — inner j-loop renders for each
    # i, then the outer loop's substitution replaces $(i) in those bodies.
    assert "TRACE 1-1;" in out
    assert "TRACE 1-2;" in out
    assert "TRACE 2-1;" in out
    assert "TRACE 2-2;" in out
    assert "$(i)" not in out
    assert "$(j)" not in out
    assert diags == []


# ---------------------------------------------------------------------------
# Full preprocess() integration
# ---------------------------------------------------------------------------


def test_preprocess_unrolls_then_expands_macros():
    """End-to-end through preprocess(): a FOR loop with $(i) in the body
    gets unrolled AND the loop var macro-expanded by the subsequent pass."""
    script = (
        "FOR i = 1 TO 3\n"
        "  LOAD * FROM 'sales_$(i).qvd';\n"
        "NEXT i\n"
    )
    with tempfile.NamedTemporaryFile(suffix=".qvs", delete=False, mode="w") as f:
        f.write(script)
        path = f.name
    pre = preprocess(path)
    # After unroll + macro-expand, the $(i) is gone and there are three
    # concrete LOAD lines.
    assert "$(i)" not in pre.text
    assert pre.text.count("LOAD * FROM 'sales_1.qvd';") == 1
    assert pre.text.count("LOAD * FROM 'sales_2.qvd';") == 1
    assert pre.text.count("LOAD * FROM 'sales_3.qvd';") == 1
