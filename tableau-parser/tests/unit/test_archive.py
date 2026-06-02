"""
.twbx archive extraction (plan §2.2 / §6 step 1).
The extractor takes a .twbx path and returns the inner .twb path.
"""
import zipfile
import pytest


def test_twbx_extracted_to_twb(fixture_path, tmp_path):
    from tableau_parser.extractor.archive import extract_twbx

    twbx = fixture_path("06_packaged_workbook.twbx")
    inner_twb = extract_twbx(twbx, dest_dir=tmp_path)

    assert inner_twb.exists()
    assert inner_twb.suffix == ".twb"
    # The inner .twb body should be valid XML starting with a workbook root
    content = inner_twb.read_text(encoding="utf-8")
    assert "<workbook" in content


def test_has_extract_flag_detected(fixture_path, tmp_path):
    """Plan §6 step 7: presence of .hyper / .tde in /Data/ sets has_extract=True."""
    from tableau_parser.extractor.archive import extract_twbx, detect_extracts

    extract_twbx(fixture_path("06_packaged_workbook.twbx"), dest_dir=tmp_path)
    extracts = detect_extracts(tmp_path)
    assert len(extracts) >= 1
    assert any(p.suffix in (".hyper", ".tde") for p in extracts)


def test_twbx_with_no_twb_raises(tmp_path):
    """Plan §14: malformed .twbx — fail fast with a clear error."""
    from tableau_parser.extractor.archive import extract_twbx, TwbxFormatError

    bad = tmp_path / "no_twb.twbx"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("Image/logo.png", b"\x89PNG")  # no .twb inside

    with pytest.raises(TwbxFormatError):
        extract_twbx(bad, dest_dir=tmp_path / "out")


def test_plain_twb_passes_through(fixture_path, tmp_path):
    """A .twb path should be returned unchanged (no extraction needed)."""
    from tableau_parser.extractor.archive import resolve_input

    twb = fixture_path("01_simple_single_datasource.twb")
    resolved = resolve_input(twb, dest_dir=tmp_path)
    assert resolved == twb
