"""構成完全性ゲート。宣言した型の『核ビート』がデッキに実在するかを出荷時に検査し、
核が無ければ型ラベルを report に降格する(「Askの無いpitch」「事例の無いcase-study」を出荷しない)。
捏造はさせない(組成は organize の接地鉄則のまま)。ラベルだけを正直に直す。課金ゼロ(claude)。
"""
from __future__ import annotations
import llm
import factcheck as _factcheck

# 型が「その型」として成立するための核(これが無ければ別物=report)
CORE = {
    "pitch": "相手への具体的な依頼(Ask)と、自社が何者で何を提供するか(自社の解決策)",
    "service-intro": "自社の提供物(サービス・製品の中身)の紹介",
    "case-study": "実在する導入主体(顧客)の、導入前(before)→導入後(after)の変化",
    "consulting": "複数の選択肢を比較したうえでの、単一の推奨結論",
}

SYSTEM = "あなたは資料の構成監査官です。宣言された型の『核』がデッキに実在するかだけを判定します。JSONのみ。"


def check(structure: str, deck: dict) -> dict:
    core = CORE.get(structure)
    if not core:   # report / education は核ビートの縛り無し
        return {"structure": structure, "effective": structure, "demoted": False, "reason": ""}
    lines = [t for _sid, t in _factcheck._slide_lines(deck) if str(t).strip()]
    body = "\n".join(f"- {t}" for t in lines)
    prompt = f"""この資料は「{structure}」型として作られました。
{structure}型が『その型』として成立するための核: {core}
下のスライドの見出し・本文に、この核が実在するかを判定してください(あるかないかだけ。無い内容を補って評価しない)。
## スライド
{body}
## 出力(JSONのみ)
{{"core_present": true/false, "reason": "根拠を一言"}}"""
    try:
        r = llm.generate_json(prompt, SYSTEM)
        present = bool(r.get("core_present", True))
        reason = str(r.get("reason", ""))
    except Exception:
        present, reason = True, ""   # 判定不能時は降格しない(安全側)
    if present:
        return {"structure": structure, "effective": structure, "demoted": False, "reason": reason}
    return {"structure": structure, "effective": "report", "demoted": True,
            "reason": f"{structure}の核({core})がデッキに無いため report に降格: {reason}"}
