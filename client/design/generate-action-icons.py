"""Extend the existing monochrome icon renderer; no fonts, emoji or runtime CSS drawing."""
import runpy
from pathlib import Path

HERE = Path(__file__).resolve().parent
render = runpy.run_path(str(HERE / "generate-tab-icons.py"), run_name="icon_library")["png"]
output = HERE.parent / "src/assets/icons"
output.mkdir(parents=True, exist_ok=True)
render("chevron-left", [[(40, 12), (21, 32), (40, 52)]], [], output=output)
render("chevron-right", [[(24, 12), (43, 32), (24, 52)]], [], output=output)
