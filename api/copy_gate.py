"""言葉づかいゲート。事実ではなく『声(ワーディング)』を宛先に照らして点検する。
内輪語・砕けすぎ・宛先に合わない専門用語/カタカナ語・宛先ズレを指摘(修正提案つき)。課金ゼロ(claude)。
factcheck(事実の接地)とは別レイヤ。
"""
from __future__ import annotations
import llm
import audience as _audience
import factcheck as _factcheck

SYSTEM = "あなたは資料の言葉づかいを宛先に合わせる編集者です。事実の正誤は見ず、言葉づかいだけを点検します。出力はJSONのみ。"


def check(aud: dict, deck: dict) -> dict:
    lines = [(sid, t) for sid, t in _factcheck._slide_lines(deck) if str(t).strip()]
    if not lines or not aud:
        return {"passed": None, "flags": []}
    body = "\n".join(f"[{sid}] {t}" for sid, t in lines)
    reader = (aud or {}).get("reader", "相手")
    prompt = f"""# 言葉づかいの点検
## 宛先・目的
{_audience.describe(aud)}
## 指示
次の見出し・本文の言葉づかいが、この宛先（{reader}）に合っているか点検してください。
- 内輪語・売り手側の砕けた言い回し（例:「刺さる」「ガチ」「バズる」「ローンチ」）、宛先に合わない専門用語・カタカナ語、砕けすぎ/丁寧すぎ、宛先ズレを指摘。
- 中身のない受け身CTA・精神論の締め（例:「ご検討ください」「お問い合わせください」「共に成長を目指してまいります」等で、何を・誰に・いつ が無いもの）も指摘。
- 事実の正誤は見ない（別工程）。言葉づかいだけ。問題のあるものだけ挙げる。無ければ空配列。
## 出力(JSONのみ)
{{"flags":[{{"slide":"<id>","text":"<問題の語句>","issue":"<なぜ宛先に合わないか・一言>","fix":"<言い換え案>"}}]}}
## 点検対象
{body}
"""
    try:
        r = llm.generate_json(prompt, SYSTEM)
        flags = r.get("flags") if isinstance(r, dict) else []
        flags = [f for f in (flags or []) if isinstance(f, dict) and str(f.get("text", "")).strip()]
        return {"passed": len(flags) == 0, "flags": flags[:12]}
    except Exception:
        return {"passed": None, "flags": []}
