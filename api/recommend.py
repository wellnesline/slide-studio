"""recommend: 元ネタから画風(theme)×構成(structure=起承転結の型)をAI推奨(理由付き)。課金ゼロ。

構成は structures.py(deckgen genres 由来の narrative arc)から選ぶ。organize がその章立てを背骨にする。
"""
from __future__ import annotations
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "engine"))
import themes as themes_mod
import llm
import structures

# 後方互換(/api/types が参照)。構成一覧を型として返す。
TYPES = [{"id": s["id"], "name": s["name"], "desc": s["framework"]} for s in structures.STRUCTURES]

SYSTEM = ("あなたはプレゼン資料のアートディレクターです。元ネタの内容に合う『画風』と『構成(話の型)』を"
          "1つずつ選び、なぜ合うのかを元ネタの性質にもとづいて短く述べます。誇張せず事実の範囲で。出力はJSONのみ。")


def recommend(source_text: str, aud: dict = None) -> dict:
    theme_list = themes_mod.list_themes()
    themes_desc = "\n".join(f"- {t['name']}: {t['display_name']}({'明朝系' if t['serif'] else 'ゴシック系'})" for t in theme_list)
    structs_desc = "\n".join(f"- {s['id']}: {s['name']}({s['framework']}) — {s['desc']}" for s in structures.STRUCTURES)
    aud_line = ""
    if aud:
        aud_line = f"\n## この資料の宛先・目的(構成・画風はこれに合わせる)\n誰に: {aud.get('reader','')} ／ 目的: {aud.get('purpose','')} ／ トーン: {aud.get('tone','')}\n"
    guide = ("\n## 選択の指針\n"
             "構成は『目的』に合わせる(安易に report を既定にしない。目的が『状況の報告』のときだけ report):\n"
             "- 資金調達/投資家 → pitch ／ 受注・成約/見込み客 → service-intro か consulting\n"
             "- 導入事例・実例で示す → case-study ／ 合意形成・社内提案 → consulting\n"
             "- 教育・解説/初心者 → education ／ 状況の報告 → report\n"
             "ただし『元ネタがその構成の核を支えられない』なら、その構成は選ばない(空洞になるため):\n"
             "  pitch=自社の解決策/実績/Askが元ネタに無いなら不可 ／ case-study=具体的な導入企業のbefore/after実数が無いなら不可 ／ "
             "consulting=推奨に足る比較材料が無いなら不可。支えられる構成が report しか無ければ report でよい。\n"
             "画風は内容の雰囲気に合わせる(温かい/人向け→warm-serif・coral-editorial、技術/先進→modern-sans・midnight、"
             "堅い提案/公式→executive-mincho・indigo-pro)。毎回 indigo を選ばない。")
    prompt = f"""# 画風と構成を選ぶ
{aud_line}## 選べる画風(theme)
{themes_desc}
## 選べる構成(structure=話の型)
{structs_desc}
{guide}
## 出力(JSONのみ)
{{"theme":"<themeのname>","structure":"<structureのid>","reason":"<宛先/目的に触れ、なぜその構成・画風かを1〜2文>"}}
## 元ネタ
{source_text[:8000]}
"""
    r = llm.generate_json(prompt, SYSTEM)
    if r.get("theme") not in {t["name"] for t in theme_list}:
        r["theme"] = themes_mod.DEFAULT_THEME
    if not structures.get(r.get("structure")):
        r["structure"] = structures.default()["id"]
    r.setdefault("reason", "元ネタの内容に合わせて選びました。")
    r["theme_display"] = next(t["display_name"] for t in theme_list if t["name"] == r["theme"])
    st = structures.get(r["structure"])
    r["structure_display"] = st["name"]
    # 後方互換フィールド
    r["type"] = r["structure"]; r["type_display"] = st["name"]
    return r
