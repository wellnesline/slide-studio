"""画風テーマのレジストリ(themes/*.yaml)。API/recommend がテーマ一覧を得るのに使う。"""
from __future__ import annotations
import pathlib
import yaml

THEMES_DIR = pathlib.Path(__file__).resolve().parents[1] / "themes"
DEFAULT_THEME = "executive-mincho"   # 未指定/失敗時の既定(一元管理)


def load(name: str) -> dict:
    return yaml.safe_load((THEMES_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


def _theme_files() -> list[pathlib.Path]:
    """themes/ の実体ファイルだけを返す。

    他の環境で作られたファイルに付く補助ファイルの残骸 (`._name.yaml`) が同じ拡張子で残っている。
    中身はリソースフォークのバイナリなので、 読むと UnicodeDecodeError になり
    テーマ一覧そのものが落ちる (実測: 0xa3 で decode 失敗)。
    """
    return [f for f in sorted(THEMES_DIR.glob("*.yaml")) if not f.name.startswith("._")]


def list_themes() -> list[dict]:
    out = []
    for f in _theme_files():
        try:
            t = yaml.safe_load(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue   # 壊れた 1 件で一覧全体を落とさない
        if not isinstance(t, dict) or "canvas" not in t or "typography" not in t:
            continue
        p = t.get("palette", {})
        out.append({
            "name": f.stem,
            "display_name": t.get("display_name", f.stem),
            "preview": {"bg": t["canvas"]["bg"], "accent": p.get("accent"),
                        "heading": p.get("heading"), "body": p.get("body")},
            "serif": t["typography"]["heading"]["family"] == "serif",
        })
    return out
