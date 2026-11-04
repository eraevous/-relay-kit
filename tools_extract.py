"""Kept for existing commands and docs: the code moved to tools/extract.py (also `relay-kit zips`)."""
import importlib.util, pathlib, sys

_spec = importlib.util.spec_from_file_location("relaykit_extract", pathlib.Path(__file__).resolve().parent / "tools" / "extract.py")
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
extract, build_zips, safe_output, ROOT, MARK = _m.extract, _m.build_zips, _m.safe_output, _m.ROOT, _m.MARK

if __name__ == "__main__":
    _m.main(sys.argv[1:])
