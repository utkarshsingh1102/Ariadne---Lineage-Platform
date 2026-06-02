"""
Build 06_packaged_workbook.twbx from 06_packaged_workbook_source.twb.

A .twbx is just a ZIP containing:
  - one .twb file at the root
  - optional /Data/ folder with .hyper / .tde extracts
  - optional /Image/ folder

Run from the fixtures/ directory:
    python make_twbx.py
"""
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "06_packaged_workbook_source.twb"
OUT = HERE / "06_packaged_workbook.twbx"

if not SRC.exists():
    sys.exit(f"missing source .twb: {SRC}")

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.write(SRC, arcname="packaged_workbook.twb")
    # Empty extract marker — parser should detect has_extract=True without
    # caring about the payload.
    zf.writestr("Data/Datasources/packaged_sales.hyper", b"\x00\x00\x00\x00")

print(f"wrote {OUT}")
