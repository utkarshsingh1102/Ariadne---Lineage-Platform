"""
Bracket-stripping utility (plan §2.3 / §6 step 4.4).
Tableau wraps identifiers in [brackets]. Strip them before storing names.
"""
import pytest


def test_strip_simple():
    from tableau_parser.utils.brackets import strip_brackets
    assert strip_brackets("[CustomerName]") == "CustomerName"


def test_strip_qualified():
    from tableau_parser.utils.brackets import strip_brackets
    # `[Federated.0abc].[Calculation_123]` → preserve dot separator
    assert strip_brackets("[Federated.0abc].[Calculation_123]") == "Federated.0abc.Calculation_123"


def test_strip_already_unbracketed():
    from tableau_parser.utils.brackets import strip_brackets
    assert strip_brackets("CustomerName") == "CustomerName"


def test_strip_preserves_inner_text():
    from tableau_parser.utils.brackets import strip_brackets
    assert strip_brackets("[Order Amount]") == "Order Amount"


def test_strip_unicode():
    """Plan §14: Unicode in field names must be preserved."""
    from tableau_parser.utils.brackets import strip_brackets
    assert strip_brackets("[Région]") == "Région"


def test_strip_empty():
    from tableau_parser.utils.brackets import strip_brackets
    assert strip_brackets("[]") == ""
    assert strip_brackets("") == ""
