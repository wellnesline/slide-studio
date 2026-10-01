#!/usr/bin/env python3
"""OFL和文フォント取得(Noto Sans JP / Noto Serif JP・glyfベースTTF)。curl不使用(urllib)。

PowerPointのフォント埋め込みは TrueType(glyf) のみ対応 → CFF(OTF)の Noto CJK ではなく、
google/fonts の可変TTF「Noto Sans JP / Noto Serif JP」を取得し、Regular(400)/Bold(700) の
静的インスタンスを作る。これを PNG(@font-face) と PPTX(埋め込み) の両方で使い parity を取る。
SIL OFL 1.1(再配布可)。 usage: python slide-studio/assets/fonts/_fetch_fonts.py
"""
import ssl, json, sys, urllib.request, pathlib
try:
    import certifi; CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    CTX = ssl.create_default_context()
HERE = pathlib.Path(__file__).resolve().parent

FAMILIES = {  # google/fonts の ofl ディレクトリ(可変TTF)
    "NotoSerifJP": "ofl/notoserifjp",
    "NotoSansJP": "ofl/notosansjp",
}
FAMILY_NAME = {"NotoSerifJP": "Noto Serif JP", "NotoSansJP": "Noto Sans JP"}

def _get(url, binary=True):
    req = urllib.request.Request(url, headers={"User-Agent": "slide-studio/1.0",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, context=CTX, timeout=90) as r:
        return r.read()

def list_dir(path):
    return json.loads(_get(f"https://api.github.com/repos/google/fonts/contents/{path}"))

def _rename(f, family, tag, suf, bold):
    """name table を RIBBI 形式のクリーンな family に書き換え(PPTX埋め込みの照合用)。"""
    sub = "Bold" if bold else "Regular"
    full = family + (" Bold" if bold else "")
    ps = f"{tag}-{suf}"
    vals = {1: family, 2: sub, 4: full, 6: ps, 16: family, 17: sub}
    name = f["name"]
    for nid, val in vals.items():
        name.setName(val, nid, 3, 1, 0x409)   # Windows
        name.setName(val, nid, 1, 0, 0)        # Mac

def instance_static(src_ttf, out_ttf, weight, family, tag, suf):
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    f = TTFont(str(src_ttf))
    if "glyf" not in f:
        raise RuntimeError(f"{src_ttf.name} は glyf を持たない(埋め込み不可のCFF?)")
    if "fvar" in f:
        instantiateVariableFont(f, {"wght": weight}, inplace=True)
    bold = weight >= 700
    try:
        f["OS/2"].usWeightClass = weight
        f["head"].macStyle = (f["head"].macStyle | 0x01) if bold else (f["head"].macStyle & ~0x01)
        f["OS/2"].fsSelection = ((f["OS/2"].fsSelection & ~0x40) | 0x20) if bold else ((f["OS/2"].fsSelection & ~0x20) | 0x40)
    except Exception:
        pass
    _rename(f, family, tag, suf, bold)
    f.save(str(out_ttf))
    fam = (f["name"].getName(1, 3, 1) or f["name"].getName(1, 1, 0))
    return fam.toUnicode() if fam else "?"

def main():
    made = []
    for tag, ofl in FAMILIES.items():
        try:
            items = list_dir(ofl)
        except Exception as e:
            print(f"NG list {ofl}: {e}"); continue
        ttfs = [i for i in items if i["name"].lower().endswith(".ttf")]
        # 可変フォント([wght])を優先
        var = next((i for i in ttfs if "wght" in i["name"].lower()), ttfs[0] if ttfs else None)
        if not var:
            print(f"NG {ofl}: .ttf が見つからない"); continue
        src = HERE / ("_var_" + var["name"].replace("[", "_").replace("]", "_"))
        try:
            src.write_bytes(_get(var["download_url"]))
        except Exception as e:
            print(f"NG dl {var['name']}: {e}"); continue
        for w, suf in [(400, "Regular"), (700, "Bold")]:
            out = HERE / f"{tag}-{suf}.ttf"
            try:
                fam = instance_static(src, out, w, FAMILY_NAME[tag], tag, suf)
                print(f"OK  {out.name:24} {out.stat().st_size:>9,}B  family={fam!r}")
                made.append(out.name)
            except Exception as e:
                print(f"NG  {out.name}: {e}")
        src.unlink(missing_ok=True)
    print(f"[{len(made)}] static TTF 作成 -> {HERE}")
    return 0 if len(made) == 4 else 1

if __name__ == "__main__":
    sys.exit(main())
