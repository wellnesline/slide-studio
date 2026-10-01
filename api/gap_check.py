"""不足チェック。目的に対して元ネタに『足りない要素』を指摘する(捏造で埋めない代わりに、追記を促す)。
例: 資金調達ピッチなのに元ネタに調達額・自社実績・チームが無い → それを列挙して利用者に提示。課金ゼロ(claude)。
"""
from __future__ import annotations
import llm

SYSTEM = "あなたは資料の構成コンサルです。目的に必要な要素のうち元ネタに欠けているものを指摘します。事実は作らず、欠落だけを挙げます。JSONのみ。"


def check(aud: dict, source_text: str) -> dict:
    aud = aud or {}
    purpose = aud.get("purpose", ""); reader = aud.get("reader", "")
    if not source_text.strip():
        return {"missing": []}
    prompt = f"""# 不足要素の指摘
この資料の目的は「{purpose}」、宛先は「{reader}」です。
この目的の資料に通常必要な要素のうち、下の『元ネタに含まれていない』ものだけを挙げてください(元ネタにある要素は挙げない)。
目安(目的): 資金調達=調達額/資金使途/トラクション(実績)/チーム/なぜ自社か。受注・成約=価格/導入の流れ/具体的な次の行動/導入効果。合意形成=施策の選択肢/単一の推奨案/体制・担当/予算/スケジュール/期待効果。報告=課題と示唆の実体。
目安(宛先の立場): 決裁者=導入価値/ROI/セキュリティ・条件。投資家=Ask(調達額)/使途/トラクション。役員=単一の推奨と意思決定材料。一般客/初心者=特別な追加要素は不要。
埋めるために事実を作ってはいけない。あくまで「元ネタに足りない」ものの列挙。
## 出力(JSONのみ)
{{"missing":["<不足している要素(短く)>"]}}  // 無ければ空配列
## 元ネタ
{source_text[:8000]}
"""
    try:
        r = llm.generate_json(prompt, SYSTEM)
        return {"missing": [str(m).strip() for m in (r.get("missing") or []) if str(m).strip()][:8]}
    except Exception:
        return {"missing": []}
