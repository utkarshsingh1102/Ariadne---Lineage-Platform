"""
Format auto-detection (plan §6 step 1).
Composer-text vs XML — by extension first, content sniff second.
"""
import pytest


def test_xml_extension_detected_as_xml(fixture_path):
    from tws_parser.parser.format_detector import detect_format
    assert detect_format(fixture_path("07_xml_export_single.xml")) == "xml"


def test_text_extension_detected_as_composer(fixture_path):
    from tws_parser.parser.format_detector import detect_format
    assert detect_format(fixture_path("01_single_schedule_single_job.txt")) == "composer"


def test_xml_content_sniffed_regardless_of_extension(tmp_path):
    """An .out file with XML inside should still route to the XML path."""
    from tws_parser.parser.format_detector import detect_format
    p = tmp_path / "weird.out"
    p.write_text("<?xml version='1.0'?><scheduleDefinitions/>")
    assert detect_format(p) == "xml"


def test_composer_content_sniffed_regardless_of_extension(tmp_path):
    from tws_parser.parser.format_detector import detect_format
    p = tmp_path / "x.dat"
    p.write_text("SCHEDULE WS#M#NAME\n:\n  J\n    SCRIPTNAME \"/x\"\n    STREAMLOGON u\nEND\n")
    assert detect_format(p) == "composer"


def test_unknown_format_raises(tmp_path):
    from tws_parser.parser.format_detector import detect_format, FormatDetectionError
    p = tmp_path / "junk.bin"
    p.write_bytes(b"\x00\x01\x02\x03")
    with pytest.raises(FormatDetectionError):
        detect_format(p)


@pytest.mark.parametrize("keyword", [
    "CPUNAME", "CALENDAR", "RESOURCE", "PROMPT", "EVENTRULE",
    "PARMS", "USEROBJ", "DOMAIN", "FOLDER",
])
def test_composer_recognised_when_lead_keyword_is_not_schedule(tmp_path, keyword):
    """Real composer exports often start with CPUNAME / CALENDAR /
    RESOURCE / PROMPT blocks before declaring any SCHEDULE. The
    classifier must recognise the full top-level keyword set, not just
    SCHEDULE — otherwise stress fixtures and real workbooks 500 with
    'Could not classify'.
    """
    from tws_parser.parser.format_detector import detect_format
    p = tmp_path / f"lead_{keyword.lower()}.txt"
    p.write_text(f"{keyword} EXAMPLE_NAME\n  \"some description\"\nEND\n")
    assert detect_format(p) == "composer"


def test_composer_recognised_after_large_comment_header(tmp_path):
    """If the file leads with a multi-KB comment banner the keyword may
    be past the original 512-byte peek window. The classifier should
    read enough to clear typical headers."""
    from tws_parser.parser.format_detector import detect_format
    banner = "#" + "-" * 80 + "\n"
    p = tmp_path / "with_banner.txt"
    p.write_text(banner * 30 + "CPUNAME WS01\nEND\n")
    assert detect_format(p) == "composer"
