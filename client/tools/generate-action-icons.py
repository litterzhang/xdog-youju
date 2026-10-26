#!/usr/bin/env python3
"""Rasterize our code-native line drawings with locally available ImageMagick.

Run: python3 client/tools/generate-action-icons.py (no Python packages required).
SVG is only an in-memory build input; clients use bundled transparent RGBA PNGs.
"""
from pathlib import Path
import shutil
import subprocess

SHAPES = {
    'search': '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/>',
    'refresh': '<path d="M20 9a8 8 0 0 0-14-3L3 9m0-5v5h5M4 15a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
    'add': '<path d="M12 4v16M4 12h16"/>',
    'copy': '<rect x="8" y="8" width="12" height="13" rx="1.5"/><path d="M5 16H3V3h13v2"/>',
    'external': '<path d="M14 3h7v7m0-7L11 13M10 4H4v16h16v-6"/>',
    'feedback': '<path d="M4 4h16v13H9l-5 4V4M8 8h8m-8 5h5"/>',
    'send': '<path d="m3 3 18 9-18 9 4-9-4-9Zm4 9h14"/>',
    'login': '<path d="M13 3h7v18h-7M3 12h12m-5-5 5 5-5 5"/>',
    'logout': '<path d="M11 3H4v18h7m-1-9h11m-5-5 5 5-5 5"/>',
    'retry': '<path d="M4 9a8 8 0 1 1 0 7M4 3v6h6"/>',
    'save': '<path d="M4 3h13l4 4v14H4V3Zm3 0v6h9V3M8 21v-7h9v7"/>',
    'check': '<path d="m4 12 5 5L20 6"/>',
    'edit': '<path d="m4 15 12-12 5 5L9 20l-6 1 1-6Zm9-9 5 5"/>',
    'filter': '<path d="M3 4h18l-7 8v7l-4 2v-9L3 4Z"/>',
    'close': '<path d="m5 5 14 14M19 5 5 19"/>',
    'chevron-left': '<path d="m15 4-8 8 8 8"/>',
    'chevron-right': '<path d="m9 4 8 8-8 8"/>',
    'play': '<path d="m7 3 14 9-14 9V3Z"/>',
    'pause': '<path d="M7 4v16M17 4v16"/>',
    'info': '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.3"/>',
}


def main():
    runtime = shutil.which('magick') or shutil.which('convert')
    if not runtime:
        raise SystemExit('ImageMagick is required to regenerate these assets.')
    target = Path(__file__).resolve().parents[1] / 'src/assets/icons'
    target.mkdir(parents=True, exist_ok=True)
    for name, shape in SHAPES.items():
        for suffix, color in [('', '#171717'), ('-subtle', '#737373'), ('-inverted', '#ffffff')]:
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 24 24">'
                   f'<g fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" '
                   f'stroke-linejoin="round">{shape}</g></svg>')
            subprocess.run([runtime, '-background', 'none', 'svg:-', '-resize', '64x64',
                            '-channel', 'RGB', '-fill', color, '-colorize', '100', '+channel',
                            '-strip', f'PNG32:{target / ("action-" + name + suffix + ".png")}'],
                           input=svg.encode(), check=True)


if __name__ == '__main__':
    main()
