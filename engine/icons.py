"""アイコン層(Lucide, ISC)。オフライン・インラインSVG・テーマ色化(currentColor不使用で直接stroke指定)。

HTML/PNG は svg_inline() でそのまま。PPTX は rasterize() で色付きPNGにして add_picture。
organize は NAMES(取得済みアイコン名)から意味アイコンを選ぶ。
"""
from __future__ import annotations
import pathlib, re

ICON_DIR = pathlib.Path(__file__).resolve().parents[1] / "assets" / "icons"
_PNG = ICON_DIR / "_png"; _PNG.mkdir(exist_ok=True)
_cache: dict[str, str] = {}

NAMES = sorted(p.stem for p in ICON_DIR.glob("*.svg"))


def has(name: str) -> bool:
    return isinstance(name, str) and (ICON_DIR / f"{name}.svg").exists()


def _inner(name: str):
    if name in _cache:
        return _cache[name]
    p = ICON_DIR / f"{name}.svg"
    if not p.exists():
        return None
    m = re.search(r"<svg[^>]*>(.*)</svg>", p.read_text(encoding="utf-8"), re.DOTALL)
    inner = m.group(1).strip() if m else ""
    _cache[name] = inner
    return inner


def svg_inline(name: str, color: str, size: int, sw: float = 2) -> str:
    inner = _inner(name)
    if inner is None:
        return ""
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">{inner}</svg>')


def rasterize(specs):
    """specs: iterable of (name,color,size). 戻り {(name,color,size): png_path}。1ブラウザで一括。"""
    specs = [s for s in set(specs) if has(s[0])]
    result, todo = {}, []
    for name, color, size in specs:
        key = _PNG / f"{name}_{color.lstrip('#')}_{size}.png"
        if key.exists():
            result[(name, color, size)] = key
        else:
            todo.append((name, color, size, key))
    if todo:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(); pg = b.new_page(device_scale_factor=3)
            for name, color, size, key in todo:
                pg.set_content(f'<!doctype html><meta charset=utf-8><style>*{{margin:0}}</style>'
                               f'<div id=i style="width:{size}px;height:{size}px;line-height:0">{svg_inline(name, color, size)}</div>')
                pg.locator("#i").screenshot(path=str(key), omit_background=True)
                result[(name, color, size)] = key
            b.close()
    return result
