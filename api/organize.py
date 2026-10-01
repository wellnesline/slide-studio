"""organize: 取り込んだソース → デザインされたスライド構成(deck.json)。課金ゼロ(claude CLI)。

原則:
- 本文は **ソースに書かれている内容だけ** を根拠にする(盛りすぎ防止)。
- **箇条書きに逃げず、内容に合ったデザイン・コンポーネントを選ぶ**(数値→big-number/chart/KPI、対照→2カラム、発言→引用…)。
- 出力は engine が描けるスキーマに限定。数値(chart/stat/kpi)は元ネタに実在する数字のみ。
"""
from __future__ import annotations
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "engine"))
import llm
import audience as _audience
import icons as _icons
ICON_NAMES = _icons.NAMES   # 取得済みLucideアイコン名(organizeはここから選ぶ)

# engine が描けるレイアウト(=デザイン・コンポーネント)。when=いつ使うか。
LAYOUTS = {
    "cover":        "表紙。要素 kicker, heading, subhead",
    "statement":    "主張・キーメッセージを1枚で言い切る。要素 kicker?, heading(=主張文)",
    "big-number":   "決定的な単一の数値が主役の時。要素 kicker, heading, stat{value,unit,caption,delta?{dir:up|down,value}}",
    "kpi-row":      "指標が2〜4個並ぶ/変化のまとめ。要素 kicker, heading, kpi{tiles:[{value,label,icon,delta?}]}(2〜4件・各tileにicon)",
    "two-column":   "二項対照(現状↔理想 / 旧↔新 / A↔B / before↔after)。要素 kicker, heading, columns{left:{title,points[]},right:{title,points[]}}",
    "icon-grid":    "並列な特長/理由/メリットを2〜4個(各アイコン+短い説明)。要素 kicker, heading, features{items:[{icon,title,body}]}(2〜4件)",
    "chart":        "数値の比較・推移・構成比。要素 kicker, heading, chart{chart:{kind:column|bar|line|pie,categories:[],series:[{name,data:[数値]}]}}, insight(1文)",
    "comparison":   "3項目以上×属性の横断比較(◯×△や短語で)。要素 kicker, heading, table{columns:[表頭。先頭は空文字\"\"],rows:[{label,cells:[各列の値。◯×△ or 短い語]}]}",
    "process":      "手順・工程・進め方(3〜5ステップ)。要素 kicker, heading, steps{items:[{title,desc}]}",
    "timeline":     "期間・マイルストーン・ロードマップ。要素 kicker, heading, timeline{items:[{date,label}]}(3〜6)",
    "matrix":       "2軸での分類・優先度(2x2マトリクス)。要素 kicker, heading, matrix{axis_x:{low,high},axis_y:{low,high},items:[{label,x,y}]}(x,yは0〜1)",
    "funnel":       "段階的な絞り込み(パイプライン/CV/採用など)。要素 kicker, heading, funnel{stages:[{label,value}]}(上ほど広い)",
    "image-lead":   "大きなビジュアル+要点。要素 kicker, heading, visual{icon,points:[]}",
    "quote":        "発言・顧客の声・レビューを実感として。要素 quote{text,name,role}",
    "section":      "話題の区切り(章扉)。要素 kicker, heading",
    "title-bullets":"上記いずれにも当てはまらない列挙(最終手段)。要素 kicker, heading, bullets{items:[](3〜5)}",
    "closing":      "締め・次の一歩。要素 kicker, heading, subhead",
}
STRUCT_KINDS = {"stat", "kpi", "columns", "quote", "chart", "features", "table", "steps", "timeline", "matrix", "funnel", "visual"}

SYSTEM = (
    "あなたは資料デザイナーです。渡された元ネタから、日本語の『デザインされたスライド構成』を作ります。"
    "厳守: (1)本文は元ネタに書かれている内容だけを根拠にし、元ネタに無い数値・固有名・実績を創作しない。"
    "(2)**箇条書き(title-bullets)に逃げない**。内容に合うコンポーネントを選ぶ: 決定的な単一数値→big-number、"
    "複数指標→kpi-row、二項対照→two-column、並列な特長/理由→icon-grid、数値比較/推移→chart、発言→quote、主張の言い切り→statement。"
    "(3)chart/stat/kpi の数値は元ネタに実在する数字だけを使い、勝手に計算・外挿・％化しない。数値が無ければそのコンポーネントを使わない。"
    "(4)kpi の各tile と icon-grid の各feature には、意味に合うアイコンを『指定アイコン一覧』から必ず選ぶ(一覧に無い名前は使わない)。"
    "(5)同じレイアウトを3枚以上連続させない・デッキ全体で最低4種のレイアウトを使う(視覚のリズム)。"
    "(6)出力は指定スキーマのJSONのみ。"
)


def build_deck(title: str, source_text: str, structure: dict = None, aud: dict = None) -> dict:
    layouts = "\n".join(f"- {k}: {v}" for k, v in LAYOUTS.items())
    aud_block = ""
    if aud:
        aud_block = ("\n## この資料の宛先・目的(言葉づかいはこれに合わせる — 最優先)\n"
                     + _audience.describe(aud) + "\n" + _audience.wording_rules(aud) + "\n")
    struct_line, target_slides = "", 6
    if structure and structure.get("chapters"):
        chs = structure["chapters"]
        struct_line = ("\n## 構成の背骨(起承転結。この章立ての順に各章を1枚のスライドにする。"
                       "元ネタに内容が無い章は飛ばし、足りなければ分割してよい)\n"
                       f"型: {structure.get('name','')} ／ {structure.get('framework','')}\n"
                       + "\n".join(f"{i+1}. {c}" for i, c in enumerate(chs)) + "\n")
        target_slides = max(6, min(len(chs), 9))
    prompt = f"""# 依頼
元ネタから、{target_slides}枚前後の『デザインされた』スライド構成を作ってください(表紙を含む)。
箇条書きだけの単調な資料にせず、内容に合うコンポーネントを選んでください。

## 鉄則(接地・最優先。破ると失敗)
- 各スライドは元ネタに実在する内容だけで作る。**元ネタに実体が無い章・スライドは作らない**(枚数合わせで水増ししない。少なくてよい)。
- 特に次の"重い実体"は、元ネタに具体があるときだけ作る。無ければその章を省く(汎用文・一般論・精神論で埋めない):
  ・pitch=調達額/資金使途・自社の解決策/差別化・自社の実績(トラクション)  ・case-study=顧客名・導入前(before)実数・導入後(after)実数
  ・consulting=単一の推奨案(どれを選ぶべきかの結論)  ・report=課題と示唆の実体(単なる数字の再掲でない解釈)
- 締めは2つを分けて考える: (a)**CTA(次の行動)**=元ネタに具体的な行動(申込先・問い合わせ・期限・次の打合せ)があるときだけ書く。無ければ作らない(捏造禁止)。(b)**示唆・結論**=デッキ内で示した事実を統合した「解釈」(何が言えるか/何を優先すべきか/どれを推すか/だから何か)。これは新しい事実の追加ではなく既出データの解釈なので、**必ず入れる**。「〇〇はそれぞれ特徴があります」「今後も注視します」のような事実再掲・中身の無い受け身では締めない。目的別に: 合意形成→推奨案と理由・誰が何を決めるか / 報告→数字から言える示唆 / 受注→相手にとっての価値と次の一歩 / 教育→要点の再構成(単なる反復でなく)。
- 同じ数字を別スライドで再掲して枚数を稼がない。表紙で約束した論点は本文で必ず回収する。
- グラフ(chart)は同一軸に単位・期間・基準の異なる系列を混ぜない(例:月額と一括料金、前年比%と実数、を同じグラフに並べない)。前年比などの指数は実数と混同させず、値には単位を添える。元ネタの根拠が無い断定(「拡大」「最も安い」等)や、デッキ内で矛盾する主張をしない。
{aud_block}{struct_line}
## 使えるレイアウト=コンポーネント(これ以外は使わない。各行の「要素」に従う)
{layouts}

## 指定アイコン一覧(kpiのtile・icon-gridのfeatureの icon はこの中から必ず選ぶ。この名前以外は使わない)
{", ".join(ICON_NAMES)}

## 出力スキーマ(JSONのみ。各スライドは layout に対応する elements を持つ)
{{"title":"<デッキ表題>","slides":[
  {{"id":"s01","layout":"cover","elements":[{{"kind":"kicker","text":"..."}},{{"kind":"heading","text":"..."}},{{"kind":"subhead","text":"..."}}]}},
  {{"id":"s02","layout":"big-number","elements":[{{"kind":"kicker","text":"..."}},{{"kind":"heading","text":"..."}},{{"kind":"stat","value":"40","unit":"時間/月","caption":"...","delta":{{"dir":"down","value":"約6割減"}}}}]}},
  {{"id":"s03","layout":"kpi-row","elements":[{{"kind":"kicker","text":"..."}},{{"kind":"heading","text":"..."}},{{"kind":"kpi","tiles":[{{"value":"1/5","label":"...","icon":"clock"}},{{"value":"全員","label":"...","icon":"users"}}]}}]}},
  {{"id":"s04","layout":"icon-grid","elements":[{{"kind":"kicker","text":"..."}},{{"kind":"heading","text":"..."}},{{"kind":"features","items":[{{"icon":"rocket","title":"...","body":"..."}},{{"icon":"shield-check","title":"...","body":"..."}}]}}]}},
  {{"id":"s05","layout":"two-column","elements":[{{"kind":"kicker","text":"..."}},{{"kind":"heading","text":"..."}},{{"kind":"columns","left":{{"title":"いまの課題","points":["...","..."]}},"right":{{"title":"導入後","points":["...","..."]}}}}]}},
  {{"id":"s06","layout":"chart","elements":[{{"kind":"kicker","text":"..."}},{{"kind":"heading","text":"..."}},{{"kind":"chart","chart":{{"kind":"column","categories":["従来","導入後"],"series":[{{"name":"時間","data":[5,1]}}]}}}},{{"kind":"insight","text":"..."}}]}},
  {{"id":"s07","layout":"quote","elements":[{{"kind":"quote","text":"...","name":"...","role":"..."}}]}}
]}}

## デッキの希望タイトル(参考)
{title or "(元ネタから適切に決める)"}

## 元ネタ
{source_text[:16000]}
"""
    return sanitize_deck(llm.generate_json(prompt, SYSTEM), title)


# ============================ 全文採用パス(圧縮度プリセット ①②③) ============================
# 現行 build_deck は単発生成=最大9枚・16k clip でどうしても端折る。ここは元ネタを章/節で
# チャンク分割し、各チャンクをスライド化して結合する。max_slides=None:①全量(枚数無制限)/
# 数値:目標枚数に収まるようチャンクを統合(内容は落とさない)。=②20枚 ③10枚。
def _chunk_looks_heading(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    if s.startswith("#") or (s.startswith("第") and "章" in s[:8]):
        return True
    return len(s) <= 28 and "。" not in s and (s.endswith("とは") or "：" in s or ":" in s)


def _chunk_source_slides(text: str, max_chars: int = 2200) -> list[str]:
    paras = [p for p in (text or "").replace("\r", "").split("\n\n") if p.strip()]
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for p in paras:
        is_head = _chunk_looks_heading(p.strip().splitlines()[0])
        if cur and (cur_len + len(p) > max_chars or (is_head and cur_len > max_chars * 0.5)):
            chunks.append("\n\n".join(cur)); cur = []; cur_len = 0
        cur.append(p); cur_len += len(p)
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks or [text]


def _regroup(chunks: list[str], n: int) -> list[str]:
    """チャンク数が目標枚数より多いとき、隣接を統合して n グループに畳む(内容は残す)。"""
    if len(chunks) <= n:
        return chunks
    per = len(chunks) / n
    groups: list[str] = []
    for i in range(n):
        lo = round(i * per); hi = round((i + 1) * per)
        groups.append("\n\n".join(chunks[lo:hi]) or chunks[min(lo, len(chunks) - 1)])
    return [g for g in groups if g.strip()]


def _distribute(chunks: list[str], body: int) -> list[int]:
    lens = [max(1, len(c)) for c in chunks]
    tot = sum(lens)
    counts = [max(1, round(body * l / tot)) for l in lens]
    while sum(counts) > body and any(c > 1 for c in counts):
        counts[counts.index(max(counts))] -= 1
    while sum(counts) < body:
        counts[counts.index(min(counts))] += 1
    return counts


def _chunk_to_slides(chunk: str, aud_block: str, layouts: str, icons: str, wanted: int | None) -> list[dict]:
    if wanted:
        count_line = (f"この内容を必ず {wanted} 枚のスライドにする。要点を統合してよいが、"
                      "重要な論点・具体例・数値を落とさない。")
    else:
        count_line = ("この内容の重要な論点・具体例・数値を漏れなくカバーする。必要なだけの枚数にする"
                      "(枚数は問わない。ただし1枚が薄くなりすぎないよう2〜4の要点でまとめる)。")
    prompt = f"""# 依頼
次の「元ネタの一部」を、デザインされたスライドにしてください。{count_line}
箇条書きだけの単調な資料にせず、内容に合うコンポーネントを選んでください。
## 鉄則(接地・最優先)
- 元ネタに実在する内容だけで作る。数値・固有名は実数のみ(創作禁止)。同じ数字を別スライドで再掲しない。
- ★各スライドは layout に対応する「内容コンポーネント」(bullets/kpi/columns/features/stat/chart/steps/quote 等)を必ず1つ以上持たせる。見出し(heading/kicker/subhead)だけの空スライドは作らない(描画時に捨てられる)。
{aud_block}
## 使えるレイアウト=コンポーネント(これ以外は使わない)
{layouts}
## 指定アイコン一覧(kpiのtile・icon-gridのfeatureの icon はこの中から必ず選ぶ)
{icons}
## 出力スキーマ(JSONのみ・cover と closing は作らない=本文スライドだけ。各slideは内容コンポーネントを必ず持つ)
{{"slides":[
  {{"id":"x","layout":"title-bullets","elements":[{{"kind":"heading","text":"見出し"}},{{"kind":"bullets","items":["要点1","要点2","要点3"]}}]}},
  {{"id":"x","layout":"two-column","elements":[{{"kind":"heading","text":"..."}},{{"kind":"columns","left":{{"title":"...","points":["...","..."]}},"right":{{"title":"...","points":["...","..."]}}}}]}},
  {{"id":"x","layout":"icon-grid","elements":[{{"kind":"heading","text":"..."}},{{"kind":"features","items":[{{"icon":"rocket","title":"...","body":"..."}},{{"icon":"shield-check","title":"...","body":"..."}}]}}]}},
  {{"id":"x","layout":"big-number","elements":[{{"kind":"heading","text":"..."}},{{"kind":"stat","value":"40","unit":"時間/月","caption":"..."}}]}},
  {{"id":"x","layout":"statement","elements":[{{"kind":"heading","text":"言い切りの主張1文"}}]}}
]}}
## 元ネタの一部
{chunk}
"""
    try:
        r = llm.generate_json(prompt, SYSTEM)
    except Exception:
        return []
    return [s for s in (r.get("slides") or []) if isinstance(s, dict) and s.get("layout")]


def build_deck_full(title: str, source_text: str, structure: dict = None,
                    aud: dict = None, max_slides: int | None = None) -> dict:
    """全文採用でデッキ生成。max_slides=None:①全量 / 20:②/ 10:③。cover と closing を前後に付す。"""
    layouts = "\n".join(f"- {k}: {v}" for k, v in LAYOUTS.items())
    icons = ", ".join(ICON_NAMES)
    aud_block = ""
    if aud:
        aud_block = ("\n## この資料の宛先・目的(言葉づかいはこれに合わせる — 最優先)\n"
                     + _audience.describe(aud) + "\n" + _audience.wording_rules(aud) + "\n")
    chunks = _chunk_source_slides(source_text)
    if max_slides is not None:
        body = max(1, max_slides - 2)            # cover + closing を確保
        if len(chunks) > body:
            chunks = _regroup(chunks, body)
        counts = _distribute(chunks, body)
    else:
        counts = [None] * len(chunks)

    body_slides: list[dict] = []
    for ch, want in zip(chunks, counts):
        sl = _chunk_to_slides(ch, aud_block, layouts, icons, want)
        if want:                                 # モデルは指定枚数を超えて量産しがち→割当でハードキャップ(②③の枚数を守る)
            sl = sl[:want]
        body_slides += sl
    if not body_slides:                          # 全滅時は従来経路にフォールバック
        return build_deck(title, source_text, structure, aud)

    # 表紙(接地・非LLM=捏造なし)
    first_para = next((p.strip() for p in (source_text or "").split("\n\n") if p.strip()), "")
    subhead = first_para.replace("\n", " ")[:46]
    cover = {"id": "cover", "layout": "cover", "elements": [
        {"kind": "kicker", "text": (structure or {}).get("name", "") or "解説"},
        {"kind": "heading", "text": title or "資料"},
        {"kind": "subhead", "text": subhead}]}

    # 締め(既出データの解釈=示唆・まとめ。1枚だけ)
    closing_prompt = f"""次の資料の締めスライド1枚だけを作ってください。新しい事実は足さず、本文で示した内容を統合した
「示唆・まとめ」(何が言えるか/何を優先すべきか)を statement か icon-grid で表現します。
## 出力(JSONのみ)
{{"slides":[{{"id":"closing","layout":"statement","elements":[{{"kind":"heading","text":"..."}}]}}]}}
## 資料の元ネタ(要点抽出用)
{source_text[:8000]}"""
    try:
        cr = llm.generate_json(closing_prompt, SYSTEM)
        closing = next((s for s in (cr.get("slides") or []) if isinstance(s, dict) and s.get("layout")), None)
    except Exception:
        closing = None

    slides = [cover] + body_slides + ([closing] if closing else [])
    for i, s in enumerate(slides, 1):            # id 振り直し
        s["id"] = f"s{i:02d}"
    return sanitize_deck({"title": title, "slides": slides}, title)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _num01(v):
    n = _num(v)
    return 0.5 if n is None else max(0.0, min(1.0, n))

def _clean_delta(d):
    if not isinstance(d, dict) or not str(d.get("value", "")).strip():
        return None
    dr = str(d.get("dir", "up")); dr = "down" if dr == "down" else "up"
    return {"dir": dr, "value": str(d["value"]).strip()}

def _clean_column(c):
    if not isinstance(c, dict):
        return {"title": "", "points": []}
    pts = []
    for p in (c.get("points") or []):
        if isinstance(p, dict):
            p = p.get("text") or p.get("label") or ""   # dictで来たらtext/labelを拾う(reprを出さない)
        p = str(p).strip()
        if p:
            pts.append(p)
    return {"title": str(c.get("title", "")).strip(), "points": pts[:6]}   # points上限6

def _clean_chart(ch):
    """chartデータを頑健化: 数値でない/None を除去、categoriesとseries.dataの長さを揃える。
    数値がゼロ本ならNone(=chart要素を落とす。捏造しない・空グラフも出さない)。"""
    if not isinstance(ch, dict):
        return None
    cats = [str(c) for c in (ch.get("categories") or [])]
    series = []
    for s in (ch.get("series") or []):
        if not isinstance(s, dict):
            continue
        data = [(_num(v) if _num(v) is not None else 0.0) for v in (s.get("data") or [])]
        if data:
            series.append({"name": str(s.get("name", "")), "data": data})
    if not cats or not series:
        return None
    m = min([len(cats)] + [len(s["data"]) for s in series])
    if m < 1:
        return None
    cats = cats[:m]
    for s in series:
        s["data"] = s["data"][:m]
    kind = ch.get("kind", "column")
    return {"kind": kind if kind in ("column", "bar", "line", "pie", "doughnut") else "column",
            "categories": cats, "series": series}

def _clean_element(e):
    if not isinstance(e, dict) or not e.get("kind"):
        return None
    k = e["kind"]
    if k == "bullets":
        items = e.get("items")
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list):
            return None
        items = [str(x).strip() for x in items if str(x).strip()]
        return {"kind": "bullets", "items": items[:6]} if items else None
    if k == "stat":
        if not str(e.get("value", "")).strip():
            return None
        return {"kind": "stat", "value": str(e["value"]).strip(), "unit": str(e.get("unit", "")).strip(),
                "caption": str(e.get("caption", "")).strip(), "delta": _clean_delta(e.get("delta"))}
    if k == "kpi":
        tiles = []
        for t in (e.get("tiles") or []):
            if not isinstance(t, dict):
                continue
            val, lab = str(t.get("value", "")).strip(), str(t.get("label", "")).strip()
            if val or lab:
                ic = t.get("icon") if t.get("icon") in ICON_NAMES else None
                tiles.append({"value": val, "label": lab, "icon": ic, "delta": _clean_delta(t.get("delta"))})
        return {"kind": "kpi", "tiles": tiles[:4]} if tiles else None
    if k == "features":
        items = []
        for it in (e.get("items") or []):
            if not isinstance(it, dict):
                continue
            title, body = str(it.get("title", "")).strip(), str(it.get("body", "")).strip()
            if title or body:
                ic = it.get("icon") if it.get("icon") in ICON_NAMES else None
                items.append({"icon": ic, "title": title, "body": body})
        return {"kind": "features", "items": items[:4]} if items else None
    if k == "table":
        cols = [str(c) for c in (e.get("columns") or [])]
        rows = []
        for r in (e.get("rows") or []):
            if not isinstance(r, dict):
                continue
            lab = str(r.get("label", "")).strip()
            cells = [str(c).strip() for c in (r.get("cells") or [])]
            if lab:
                rows.append({"label": lab, "cells": cells})
        return {"kind": "table", "columns": cols, "rows": rows} if (len(cols) >= 2 and rows) else None
    if k == "steps":
        items = []
        for it in (e.get("items") or []):
            if not isinstance(it, dict):
                continue
            title = str(it.get("title", "")).strip()
            if title:
                items.append({"title": title, "desc": str(it.get("desc", "")).strip()})
        return {"kind": "steps", "items": items[:5]} if items else None
    if k == "timeline":
        items = []
        for it in (e.get("items") or []):
            if not isinstance(it, dict):
                continue
            label, date = str(it.get("label", "")).strip(), str(it.get("date", "")).strip()
            if label or date:
                items.append({"date": date, "label": label})
        return {"kind": "timeline", "items": items[:6]} if items else None
    if k == "matrix":
        def _ax(a):
            a = a if isinstance(a, dict) else {}
            return {"low": str(a.get("low", "")).strip(), "high": str(a.get("high", "")).strip()}
        items = []
        for it in (e.get("items") or []):
            if isinstance(it, dict) and str(it.get("label", "")).strip():
                items.append({"label": str(it["label"]).strip(), "x": _num01(it.get("x")), "y": _num01(it.get("y"))})
        return {"kind": "matrix", "axis_x": _ax(e.get("axis_x")), "axis_y": _ax(e.get("axis_y")), "items": items[:6]} if items else None
    if k == "funnel":
        stages = []
        for st in (e.get("stages") or []):
            if isinstance(st, dict) and str(st.get("label", "")).strip():
                v = _num(st.get("value"))
                stages.append({"label": str(st["label"]).strip(), "value": v if v is not None else ""})
        return {"kind": "funnel", "stages": stages[:5]} if stages else None
    if k == "visual":
        pts = [str(p).strip() for p in (e.get("points") or []) if str(p).strip()]
        ic = e.get("icon") if e.get("icon") in ICON_NAMES else None
        return {"kind": "visual", "icon": ic, "points": pts[:5]} if (pts or ic) else None   # 4件超は落とす
    if k == "columns":
        left, right = _clean_column(e.get("left")), _clean_column(e.get("right"))
        if not (left["points"] or right["points"] or left["title"] or right["title"]):
            return None
        return {"kind": "columns", "left": left, "right": right}
    if k == "quote":
        if not str(e.get("text", "")).strip():
            return None
        return {"kind": "quote", "text": str(e["text"]).strip(),
                "name": str(e.get("name", "")).strip(), "role": str(e.get("role", "")).strip()}
    if k == "chart":
        c = _clean_chart(e.get("chart"))
        return {"kind": "chart", "chart": c} if c else None
    # text 系(kicker/heading/subhead/insight/statement)
    text = e.get("text")
    text = "" if text is None else str(text).strip()
    return {"kind": k, "text": text} if text else None


def sanitize_deck(deck: dict, fallback_title: str = "スライド") -> dict:
    """LLM/PATCH 両方の deck を engine が安全に描ける形に矯正する。"""
    if not isinstance(deck, dict) or not isinstance(deck.get("slides"), list):
        raise ValueError("不正なdeck(slides無し)")
    title = deck.get("title") or fallback_title or "スライド"
    clean = []
    for i, sl in enumerate(deck.get("slides", []), 1):
        if not isinstance(sl, dict):
            continue
        lay = sl.get("layout")
        if lay not in LAYOUTS:
            lay = "title-bullets"
        els = [c for c in (_clean_element(e) for e in sl.get("elements", []) or []) if c]
        # 表紙に、題とまったく同じ副題が並ぶことがある(素材の見出しを題に採ると、
        # 同じ行が副題にも入るため)。同じ文字列を2度出しても情報が増えないので落とす。
        if lay == "cover":
            els = [e for e in els
                   if not (e.get("kind") == "subhead"
                           and (e.get("text") or "").strip() == title.strip())]
        if not els:
            continue
        # 中身の無いスライド(見出し系だけ=コンポーネントが全部落ちた空枠)は出荷しない
        if lay not in ("cover", "closing", "section", "statement"):
            if not any(e.get("kind") not in ("kicker", "heading", "subhead", "insight") for e in els):
                continue
        clean.append({"id": str(sl.get("id") or f"s{i:02d}"), "layout": lay,
                      "elements": els, "source_ref": sl.get("source_ref")})
    if not clean:
        raise ValueError("有効なスライドが0")
    return {"title": title, "slides": clean}


def build_slide(source_text: str, layout: str, topic: str = "") -> dict | None:
    """1枚だけ、指定レイアウトで作り直す(型切替UI用)。元ネタに接地。"""
    lv = LAYOUTS.get(layout, "")
    prompt = f"""元ネタから、レイアウト「{layout}」1枚分の要素だけを作ってください。
このスライドの話題: {topic or "(元ネタから適切に)"}
## レイアウト定義(この要素構成に従う)
{layout}: {lv}
## 指定アイコン一覧(kpi/featureのiconはこの中から)
{", ".join(ICON_NAMES)}
## 厳守
元ネタに実在する内容だけを使う。数値は元ネタの実数のみ、創作しない。
必ず kicker と heading を含め、それに加えてレイアウト定義の主要素を作る。
## 出力(JSONのみ)
{{"layout":"{layout}","elements":[ {{"kind":"kicker","text":"..."}}, {{"kind":"heading","text":"..."}}, ... レイアウト定義の主要素 ... ]}}
## 元ネタ
{source_text[:10000]}
"""
    r = llm.generate_json(prompt, SYSTEM)
    els = r.get("elements", []) if isinstance(r, dict) else []
    if topic and not any(isinstance(e, dict) and e.get("kind") == "heading" for e in els):
        els.insert(0, {"kind": "heading", "text": topic})   # heading を保証(空スライド化を防ぐ)
    try:
        sd = sanitize_deck({"title": "x", "slides": [{"id": "s", "layout": layout, "elements": els}]}, "x")
    except ValueError:
        return None
    if not sd.get("slides"):
        return None
    sl = sd["slides"][0]
    # 見出し系だけで主要素(構造/箇条書き)が無い＝その型に必要なデータが元ネタに無い → 失敗扱い
    HEADERISH = {"kicker", "heading", "subhead", "insight", "statement"}
    if layout not in ("cover", "closing", "section", "statement") and \
       not any(isinstance(e, dict) and e.get("kind") not in HEADERISH for e in sl.get("elements", [])):
        return None
    return sl


# 後方互換: 旧名を参照している箇所向け
ALLOWED_LAYOUTS = LAYOUTS
