"""盛りすぎチェック(グラウンディング・ゲート)。課金ゼロ(claude CLI)。

スライドの各記述のうち、元ネタで裏付けられない **具体的な事実**(数値/割合/金額/日付/固有名詞/実績)
を検出する。UIの「盛りすぎチェック通過」表示に実体を与える(prompt指示だけの空約束にしない)。
"""
from __future__ import annotations
import llm

SYSTEM = ("あなたは資料の事実確認担当です。スライドの記述が元ネタで裏付けられるかを厳密に確認します。"
          "一般的な言い回し・要約・提案表現は対象外。元ネタに存在しない具体的な事実(数値/割合/金額/日付/"
          "固有名詞/定量的な実績)だけを指摘します。判断に迷うものは指摘しない。出力はJSONのみ。")


def _slide_lines(deck: dict):
    """スライドの全記述(chart/stat/kpiの数値も含む)を (slide_id, テキスト) で列挙。
    ★chart/stat/kpi の数値まで対象にしないと、捏造グラフ/捏造KPIが盛りすぎチェックを素通りする。"""
    for s in deck.get("slides", []):
        sid = s.get("id", "")
        for e in s.get("elements", []):
            k = e.get("kind")
            if k == "bullets":
                for it in e.get("items", []):
                    yield sid, it
            elif k == "stat":
                d = (e.get("delta") or {}).get("value", "")
                yield sid, f"{e.get('value','')}{e.get('unit','')} {e.get('caption','')} {d}".strip()
            elif k == "kpi":
                for t in e.get("tiles", []) or []:
                    d = (t.get("delta") or {}).get("value", "")
                    yield sid, f"{t.get('value','')} {t.get('label','')} {d}".strip()
            elif k == "columns":
                for side in ("left", "right"):
                    c = e.get(side) or {}
                    if c.get("title"):
                        yield sid, c["title"]
                    for p in c.get("points", []) or []:
                        yield sid, p
            elif k == "quote":
                if e.get("text"):
                    yield sid, e["text"]
                if e.get("name") or e.get("role"):
                    yield sid, f"{e.get('name','')} {e.get('role','')}".strip()
            elif k == "features":
                for it in e.get("items", []) or []:
                    yield sid, f"{it.get('title','')} {it.get('body','')}".strip()
            elif k == "table":
                for c in e.get("columns", []) or []:
                    if str(c).strip():
                        yield sid, str(c)
                for r in e.get("rows", []) or []:
                    yield sid, f"{r.get('label','')}: {', '.join(str(x) for x in r.get('cells', []))}"
            elif k == "steps":
                for it in e.get("items", []) or []:
                    yield sid, f"{it.get('title','')} {it.get('desc','')}".strip()
            elif k == "timeline":
                for it in e.get("items", []) or []:
                    yield sid, f"{it.get('date','')} {it.get('label','')}".strip()
            elif k == "matrix":
                for it in e.get("items", []) or []:
                    yield sid, str(it.get("label", ""))
            elif k == "funnel":
                for st in e.get("stages", []) or []:
                    yield sid, f"{st.get('label','')} {st.get('value','')}".strip()
            elif k == "visual":
                for pnt in e.get("points", []) or []:
                    yield sid, str(pnt)
            elif k == "chart":
                ch = e.get("chart") or {}
                cats = ch.get("categories", []) or []
                for ser in ch.get("series", []) or []:
                    data = ser.get("data", []) or []
                    pairs = ", ".join(f"{cats[i] if i < len(cats) else ''}={data[i]}" for i in range(len(data)))
                    yield sid, f"{ser.get('name','')}: {pairs}"
            elif e.get("text"):
                yield sid, e["text"]


def check(source_text: str, deck: dict) -> dict:
    lines = list(_slide_lines(deck))
    if not lines:
        return {"passed": True, "flags": []}
    listing = "\n".join(f"[{sid}] {t}" for sid, t in lines)
    prompt = f"""スライドの各記述のうち、**元ネタで裏付けられない具体的な事実**(数値・割合・金額・日付・固有名詞・定量的実績)だけを挙げてください。
元ネタに書かれている、または元ネタの要約・言い換えなら指摘しない。

## 出力(JSONのみ)
{{"flags":[{{"slide":"<スライドID>","claim":"<該当記述>","why":"<元ネタに無い理由>"}}]}}
指摘が無ければ {{"flags":[]}}。

## 元ネタ
{source_text[:12000]}

## スライドの記述
{listing}
"""
    try:
        r = llm.generate_json(prompt, SYSTEM)
    except Exception as e:
        # ゲート自体が失敗したら「未確認」を正直に返す(通過を偽装しない)
        return {"passed": None, "flags": [], "error": str(e)[:200]}
    flags = r.get("flags", []) if isinstance(r, dict) else []
    flags = [f for f in flags if isinstance(f, dict) and f.get("claim")]
    return {"passed": len(flags) == 0, "flags": flags}
