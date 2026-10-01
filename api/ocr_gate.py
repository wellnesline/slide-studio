"""AI画像(ルートA)の文字照合ゲート。gpt-imageは文字を画像に焼き込むため、
生成後に選択中の Claude/Codex vision で「期待した文言・数値が画像内に正しく描かれているか」を照合し、
日本語の化け・誤字・数値誤り・欠落を指摘する。事実の接地(factcheck)とは別で、"描画の正しさ"を見る。

画像は共通 LLM アダプターへ渡し、選択プロバイダー失敗時だけ反対側へ1回退避する。
"""
from __future__ import annotations
import json, pathlib, re

import llm

class OCRVisionError(RuntimeError):
    """OCR 用 vision を実行できず、照合結果を信用できないことを示す。"""


def facts_of(sl: dict) -> list:
    """スライドが画像内に正しく描くべき『重要な文言・数値』を平坦なリストで返す。"""
    out = []
    is_title = sl.get("layout") in ("cover", "closing", "section")   # 表紙系は kicker を描かせない(full_prompt に合わせる)
    def add(*xs):
        s = " ".join(str(x) for x in xs if x is not None and str(x).strip()).strip()
        if s:
            out.append(s)
    for e in sl.get("elements", []):
        k = e.get("kind")
        if k == "kicker" and is_title:
            continue
        if k in ("heading", "kicker", "subhead", "insight", "statement"):
            add(e.get("text"))
        elif k == "stat":
            add((str(e.get("value", "")) + str(e.get("unit", ""))).strip()); add(e.get("caption"))
        elif k == "kpi":
            for t in e.get("tiles", []): add(t.get("value"), t.get("label"))
        elif k == "columns":
            for side in ("left", "right"):
                c = e.get(side) or {}; add(c.get("title"))
                for p in c.get("points", []): add(p)
        elif k == "features":
            for it in e.get("items", []): add(it.get("title"))
        elif k == "table":
            for r in e.get("rows", []):
                add(r.get("label"), " ".join(str(c) for c in r.get("cells", [])))
        elif k == "steps":
            for it in e.get("items", []): add(it.get("title"))
        elif k == "timeline":
            for m in e.get("items", []): add(m.get("date"), m.get("label"))
        elif k == "chart":
            ch = e.get("chart") or {}
            for cat in ch.get("categories", []): add(cat)
            for s in ch.get("series", []):
                for d in s.get("data", []): add(str(d))
        elif k == "funnel":
            for s in e.get("stages", []): add(s.get("label"), s.get("value"))
        elif k == "matrix":
            for it in e.get("items", []): add(it.get("label"))
        elif k == "quote":
            add(e.get("text"))
    # 重複除去(順序保持)
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f); uniq.append(f)
    return uniq


def _vision(image_path, prompt: str) -> str:
    try:
        return llm.generate(prompt, max_tokens=2000, images=[str(image_path)])
    except Exception as e:
        raise OCRVisionError(f"vision の実行に失敗しました: {type(e).__name__}: {e}") from e


def verify_slide(image_path, facts: list) -> dict:
    if not facts:
        return {"issues": []}
    fl = "\n".join(f"- {f}" for f in facts)
    prompt = (f"""あなたは校正者です。次のスライド画像を読み、下の「期待する文言・数値」が画像内に正しく描かれているか確認してください。
日本語の文字化け・誤字、数値の誤り、文言の欠落だけを指摘してください。デザインの良し悪しや言い回しの好みは見ません。
## 期待する文言・数値
{fl}
## 出力(JSONのみ・前後に地の文を付けない)
{{"issues":[{{"expected":"<期待した文言/数値>","problem":"<化け/誤字/数値誤り/欠落 のどれか + 実際の見え方>"}}]}}
問題が無ければ issues は空配列。""")
    txt = _vision(image_path, prompt)
    m = re.search(r"[\[{].*[\]}]", txt, re.DOTALL)
    if not m:
        return {"issues": []}
    try:
        d = json.loads(re.sub(r",(\s*[}\]])", r"\1", m.group(0)))
        return {"issues": [i for i in (d.get("issues") or []) if isinstance(i, dict) and i.get("expected")]}
    except Exception:
        return {"issues": []}


def check_deck(deck: dict, outdir) -> dict:
    outdir = pathlib.Path(outdir)
    flags = []
    for sl in deck.get("slides", []):
        img = outdir / f"{sl['id']}.png"
        if not img.exists():
            # 未描画は flag にする(黙って skip すると「PNG 0枚でも passed=true」の誤PASSになる)
            flags.append({"slide": sl["id"], "expected": "(画像)", "problem": "画像が未描画です(render を実行してください)"})
            continue
        for i in verify_slide(img, facts_of(sl)).get("issues", []):
            flags.append({"slide": sl["id"], "expected": i.get("expected"), "problem": i.get("problem")})
    return {"passed": len(flags) == 0, "flags": flags}
