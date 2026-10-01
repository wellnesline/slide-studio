#!/usr/bin/env python3
"""フォント: サブセット化 + PPTX埋め込み(OOXML)。

parity の核: デッキで使う文字だけに subset した「同一バイト」を
  - PNG:  HTML @font-face(base64)
  - PPTX: p:embeddedFont(fntdata)
の両方に使う → どの環境で開いても PNG と同じ書体で表示される。

PowerPoint のフォント埋め込みは TrueType(glyf) のみ対応 → assets/fonts の Noto*JP は glyf TTF。
"""
from __future__ import annotations
import io, pathlib, zipfile
from lxml import etree

FONT_DIR = pathlib.Path(__file__).resolve().parents[1] / "assets" / "fonts"

# theme の family キーワード -> (実family名, ファイル)。Regular/Bold。
FAMILY_NAME = {"serif": "Noto Serif JP", "sans": "Noto Sans JP", "mono": "Noto Sans JP"}
_FILES = {
    ("Noto Serif JP", False): "NotoSerifJP-Regular.ttf",
    ("Noto Serif JP", True):  "NotoSerifJP-Bold.ttf",
    ("Noto Sans JP", False):  "NotoSansJP-Regular.ttf",
    ("Noto Sans JP", True):   "NotoSansJP-Bold.ttf",
}
# HTML フォールバック(万一 @font-face が効かない時)
# ※ style="..." 属性(二重引用符)内に入るので、書体名は単一引用符で囲む(二重引用符だと属性が壊れる)
HTML_FALLBACK = {
    "Noto Serif JP": "'Hiragino Mincho ProN','Yu Mincho',serif",
    "Noto Sans JP":  "'Hiragino Kaku Gothic ProN','Yu Gothic',sans-serif",
}
_ALWAYS = set(" 　0123456789.,%％()（）「」、。ー・：:／/①②③④⑤▲▼“”-−＋")


def family_of(family_kw: str) -> str:
    return FAMILY_NAME.get(family_kw, "Noto Sans JP")


def subset_bytes(family: str, bold: bool, text: str) -> bytes:
    """family/bold のフォントを text の文字だけに subset して bytes を返す。"""
    from fontTools.ttLib import TTFont
    from fontTools.subset import Subsetter, Options
    path = FONT_DIR / _FILES[(family, bold)]
    f = TTFont(str(path))
    opts = Options()
    opts.name_IDs = [0, 1, 2, 3, 4, 5, 6, 16, 17]   # family名を残す(埋め込み照合のため)
    opts.name_legacy = True
    opts.recalc_timestamp = False
    opts.drop_tables = []
    ss = Subsetter(options=opts)
    ss.populate(unicodes=[ord(c) for c in (set(text) | _ALWAYS)])
    ss.subset(f)
    buf = io.BytesIO()
    f.save(buf)
    return buf.getvalue()


# ---------------- PPTX 埋め込み(OOXML) ----------------
_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_PR = "http://schemas.openxmlformats.org/package/2006/relationships"
_P  = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R  = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_FONT_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font"


def embed_fonts_into_pptx(pptx_path: pathlib.Path, fonts_by_family: dict) -> None:
    """fonts_by_family = { "Noto Serif JP": {"regular": b"..", "bold": b".."}, ... }"""
    src = zipfile.ZipFile(str(pptx_path), "r")
    entries = {i.filename: src.read(i.filename) for i in src.infolist()}
    src.close()

    # 1) [Content_Types].xml に fntdata Default を追加
    ct = etree.fromstring(entries["[Content_Types].xml"])
    if not any(d.get("Extension") == "fntdata" for d in ct.findall(f"{{{_CT}}}Default")):
        d = etree.SubElement(ct, f"{{{_CT}}}Default")
        d.set("Extension", "fntdata"); d.set("ContentType", "application/x-fontdata")
    entries["[Content_Types].xml"] = etree.tostring(ct, xml_declaration=True, encoding="UTF-8", standalone=True)

    # 2) font parts + presentation.xml.rels
    rels = etree.fromstring(entries["ppt/_rels/presentation.xml.rels"])
    existing = [int(r.get("Id")[3:]) for r in rels if r.get("Id", "").startswith("rId") and r.get("Id")[3:].isdigit()]
    rid = max(existing) + 1 if existing else 1

    lst = etree.Element(f"{{{_P}}}embeddedFontLst")
    fidx = 1
    for family, variants in fonts_by_family.items():
        ef = etree.SubElement(lst, f"{{{_P}}}embeddedFont")
        fe = etree.SubElement(ef, f"{{{_P}}}font"); fe.set("typeface", family)
        for style_tag, key in (("regular", "regular"), ("bold", "bold")):
            data = variants.get(key)
            if not data:
                continue
            part = f"ppt/fonts/font{fidx}.fntdata"; fidx += 1
            entries[part] = data
            rId = f"rId{rid}"; rid += 1
            rel = etree.SubElement(rels, f"{{{_PR}}}Relationship")
            rel.set("Id", rId); rel.set("Type", _FONT_RT); rel.set("Target", f"fonts/font{fidx-1}.fntdata")
            se = etree.SubElement(ef, f"{{{_P}}}{style_tag}")
            se.set(f"{{{_R}}}id", rId)
    entries["ppt/_rels/presentation.xml.rels"] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)

    # 3) presentation.xml: embedTrueTypeFonts + embeddedFontLst(notesSz の後)
    pres = etree.fromstring(entries["ppt/presentation.xml"])
    pres.set("embedTrueTypeFonts", "1")
    anchor = pres.find(f"{{{_P}}}notesSz")
    if anchor is None:
        anchor = pres.find(f"{{{_P}}}sldSz")
    if anchor is not None:
        anchor.addnext(lst)
    else:
        pres.append(lst)
    entries["ppt/presentation.xml"] = etree.tostring(pres, xml_declaration=True, encoding="UTF-8", standalone=True)

    # 4) 書き戻し
    with zipfile.ZipFile(str(pptx_path), "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
