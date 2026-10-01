"""デザイン採点ゲート。生成後にデッキの『デザイン品質』を採点し、低ければ自己改善する。

- score(deck): ヒューリスティック採点(0-100・即時・決定的)。多様性/ビジュアル比率/箇条書き比率。
- improve(deck, source): 採点が低いとき claude で見せ方を改善(事実は保持)。研究の generate→score→regenerate。
課金ゼロ(claude CLI)。
"""
from __future__ import annotations
import json
import llm
import organize

VISUAL = {"stat", "kpi", "columns", "chart", "table", "steps", "timeline", "features", "quote"}
STRUCTURAL = {"cover", "closing", "section", "statement"}


def score(deck: dict) -> dict:
    slides = deck.get("slides", []) if isinstance(deck, dict) else []
    if not slides:
        return {"score": 0, "issues": ["スライドがありません"]}
    layouts = [s.get("layout", "") for s in slides]
    n = len(slides); distinct = len(set(layouts))
    bullet = sum(1 for l in layouts if l == "title-bullets")
    content = [s for s in slides if s.get("layout") not in STRUCTURAL]
    visual = sum(1 for s in content if any(e.get("kind") in VISUAL for e in s.get("elements", [])))
    RICH = {"chart", "table", "steps", "timeline", "matrix", "funnel", "quote", "features", "stat"}
    rich = sum(1 for s in content if any(e.get("kind") in RICH for e in s.get("elements", [])))
    variety = distinct / n
    vr = (visual / len(content)) if content else 1.0
    rr = (rich / len(content)) if content else 0.0
    br = bullet / n
    # 図表(chart/table/timeline/process等)の使用も加点=数値カードと2カラムだけの単調さを検出
    sc = round(variety * 24 + vr * 34 + (1 - min(br * 2.5, 1)) * 18 + rr * 24)
    issues = []
    if br > 0.3:
        issues.append(f"箇条書きスライドが多い({bullet}/{n})")
    if distinct < 4 and n >= 5:
        issues.append(f"レイアウトの種類が少ない({distinct}種)")
    if content and vr < 0.6:
        issues.append("ビジュアル要素の少ないスライドがある")
    for i in range(1, len(layouts)):
        if layouts[i] == layouts[i - 1]:
            issues.append(f"同じレイアウトが連続({layouts[i]})")
            break
    return {"score": sc, "variety": round(variety, 2), "visual_ratio": round(vr, 2),
            "bullet_slides": bullet, "issues": issues}


IMPROVE_SYSTEM = (
    "あなたは資料デザイナーです。与えられたスライド構成を『デザイン品質』の観点で改善します。"
    "厳守: 元の内容(数値・固有名・事実)を保持し、新しい事実を足さない。改善は見せ方のみ: "
    "箇条書き(title-bullets)を内容に合うコンポーネントに置き換える、同じレイアウトの連続を解消する、"
    "kpi/feature に意味アイコンを付ける、レイアウトの種類を増やす。出力はスキーマJSONのみ。")


def improve(deck: dict, source_text: str):
    """戻り: (改善後deck, before_score, after_score)。既に高品質なら何もしない。"""
    before = score(deck)
    if before["score"] >= 82 and not before["issues"]:
        return deck, before, before
    layouts = "\n".join(f"- {k}: {v}" for k, v in organize.LAYOUTS.items())
    prompt = f"""# デザイン改善
次のスライド構成を、内容(事実)を一切変えずに、より『デザインされた』構成に直してください。
特に: 箇条書きを内容に合うコンポーネントへ / 同一レイアウトの連続を解消 / kpi・feature にアイコン付与 / レイアウトの種類を増やす。
現状の指摘: {', '.join(before['issues']) or 'なし'}

## 使えるレイアウト=コンポーネント(これ以外は使わない)
{layouts}

## 指定アイコン一覧(iconはこの中から)
{", ".join(organize.ICON_NAMES)}

## 元ネタ(この事実の範囲でのみ。新しい数値・固有名を足さない)
{source_text[:8000]}

## 改善対象の構成(これを直す)
{json.dumps(deck, ensure_ascii=False)}

## 出力: 改善後のdeck {{"title":...,"slides":[...]}} をJSONのみで。
"""
    try:
        improved = organize.sanitize_deck(llm.generate_json(prompt, IMPROVE_SYSTEM), deck.get("title", "スライド"))
    except Exception:
        return deck, before, before   # 改善失敗時は原本を保持
    after = score(improved)
    # 改善で悪化したら採用しない
    if after["score"] < before["score"]:
        return deck, before, before
    return improved, before, after
