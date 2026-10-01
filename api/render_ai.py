"""AI画像形式の描画: gpt-image-2 が『オンブランドの背景アート』を生成し、
正確なテキスト/数値は既存エンジン(build)で上に重ねる(ハイブリッド)。

なぜハイブリッドか: 文字まで gpt-image に描かせると日本語崩れ + 数値の捏造で
盛りすぎチェック(接地)が担保できない(画像モデルが日本語の崩れや数値の誤りを起こしうるのと同じ理由)。
→ 背景=AI / 文字=エンジン、で「AIらしい絵」と「正確な内容」を両立する。

課金: gpt-image-2.5-sunburst(quality=low)。**承認(approved)なしでは絶対に呼ばない**。
"""
from __future__ import annotations
import base64, json, os, sys, pathlib, urllib.request

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "engine"))
sys.path.insert(0, str(_ROOT.parent / "src"))
# OPENAI_API_KEY を .env から(自己完結ローダ。シェルの空/ダミーキー居座りは非空値で上書き)
try:
    sys.path.insert(0, str(_ROOT / "api"))
    from _env import load_env as _load_env
    _load_env()
except Exception:
    pass
import build as engine

from openai_image_config import (  # noqa: E402
    DEFAULT_IMAGE_MODEL,
    DEFAULT_IMAGE_QUALITY,
    validate_image_defaults,
)

MODEL = DEFAULT_IMAGE_MODEL
QUALITY = DEFAULT_IMAGE_QUALITY
validate_image_defaults(MODEL, QUALITY)
SIZE = "1280x720"              # スライド原寸(16:9)
USD_JPY = 158
PRICE_OUT_USD_PER_TOKEN = 40.0 / 1_000_000   # 画像出力トークン $40/1M(目安)
PRICE_IN_USD_PER_TOKEN = 5.0 / 1_000_000     # テキスト入力トークン $5/1M
# low 品質・1280x720 の事前概算(実測は API の usage で置換)。
COST_PER_IMAGE_USD = 0.011


def key_present() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _cost_from_usage(usage: dict) -> float:
    ot = (usage or {}).get("output_tokens", 0) or 0
    it = (usage or {}).get("input_tokens", 0) or 0
    return ot * PRICE_OUT_USD_PER_TOKEN + it * PRICE_IN_USD_PER_TOKEN


def estimate(n: int) -> dict:
    usd = n * COST_PER_IMAGE_USD
    return {"images": n, "usd_per_image": COST_PER_IMAGE_USD,
            "usd": round(usd, 3), "jpy": round(usd * USD_JPY, 1), "note": "low品質の事前概算。実コストは生成時に実測"}


_ROLE_MOOD = {
    "cover":   "a bold hero composition: one large soft gradient orb and gentle flowing ribbons, lots of open space",
    "closing": "a calm closing composition with a soft gradient glow in one corner, mostly empty",
    "section": "a quiet chapter-divider composition, a single soft band of color, very minimal",
}
_CONTENT_MOOD = ("very subtle and mostly empty (about 90 percent clean space), only a faint soft gradient "
                 "or a thin geometric accent in one corner, so overlaid text stays perfectly readable")


def bg_prompt(layout: str, theme: dict) -> str:
    pal = theme.get("palette", {}); base = theme.get("canvas", {}).get("bg", "#ffffff")
    accent = pal.get("accent", "#2f6f5f")
    mood = _ROLE_MOOD.get(layout, _CONTENT_MOOD)
    return (f"A high-end, minimal presentation slide background, 16:9. {mood}. "
            f"Refined palette built around accent color {accent} on a {base} base, with soft tints of it. "
            f"Flat vector aesthetic, smooth gradients, soft geometric shapes, elegant corporate feel. "
            f"Absolutely no text, no words, no letters, no numbers, no charts, no logos, no UI elements. "
            f"Just an abstract atmospheric background.")


def _gen_bg(prompt: str):
    """戻り: (png_bytes, usage_dict)。usage は実コスト算出用。"""
    key = os.environ["OPENAI_API_KEY"]
    body = json.dumps({"model": MODEL, "prompt": prompt, "size": SIZE, "n": 1, "quality": QUALITY}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/images/generations", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=200) as r:
        data = json.load(r)
    return base64.b64decode(data["data"][0]["b64_json"]), (data.get("usage") or {})


def _scrim(hexbg: str, alpha: float) -> str:
    h = hexbg.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ============================================================
# ルートA: 全面委譲(元 deckgen 方式) — gpt-image が1枚まるごとデザイン
#   事実(数値・文言)はこちらが指示書で固定、絵作りは全部デザイナーに委ねる
# ============================================================
FULL_SIZE = "1536x1024"   # 3:2寄り。2048x1152不可環境の保険(gpt-image-2が受理する近い16:9)

# 画風=アートディレクション文(トーンと性格は与えるが、構図・装飾はデザイナーに委ねる)
VISUAL_STYLE = {
    "executive-mincho": "品格ある日本語エディトリアルのプレゼン。明朝体の見出しを主役に、たっぷりの余白と静かなリズム。深い緑を差し色、白〜生成りの上質な紙のような背景。端正で知的、上質な印刷物の趣。",
    "modern-sans": "クリーンでモダンなテック企業のプレゼン。角ゴシックの明快な階層、青のアクセント、白基調。フラットで整理された図・カード・アイコン、機能美。余白と整列で信頼感。",
    "warm-serif": "あたたかみのあるブランドプレゼン。セリフ見出し＋やわらかい配色、生成り背景。少しだけ有機的な曲線とソフトな陰影、手触りのある上品さ。親しみと信頼の両立。",
    "midnight": "ダークモダン。濃紺〜黒の背景にシアン/ティールのネオンアクセント、白文字。発光するグラデーションとシャープな幾何、余白の効いた高級感。プレミアムなSaaSの世界観。",
    "coral-editorial": "雑誌のようなエディトリアル。大きなセリフ見出し、コーラル/テラコッタの差し色、温かい白背景。大胆な級数対比とレイアウトの遊び、抜けのある余白。洗練とヒューマンさ。",
    "indigo-pro": "洗練されたコンサル/プロダクト提案。インディゴ基調＋クリーンな白、角ゴシックの明快な階層。整ったグリッドと上質なカード・図、抑制の効いた装飾。知的で信頼感のある佇まい。",
}
_DEFAULT_STYLE = "端正でプロフェッショナルなビジネスプレゼン。フラットデザイン、整った配色と余白、1スライド1メッセージ。"


def export_pptx_images(png_paths, out_path):
    """AI画像形式の PPTX: 各スライド=全面に画像を貼る(編集不可・見た目そのまま)。"""
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation(); prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    for png in png_paths:
        s = prs.slides.add_slide(blank)
        s.shapes.add_picture(str(png), 0, 0, width=prs.slide_width, height=prs.slide_height)
    prs.save(str(out_path))
    return pathlib.Path(out_path)


def _num_or(v, alt=""):
    return str(v) if str(v).strip() else alt


def describe_slide(sl: dict) -> tuple:
    """構造化スライド → (章ラベル, アクションタイトル, 本文の描画指示)。事実(数値/文言)をそのまま埋める。"""
    kicker = title = sub = ""
    body = []
    for e in sl.get("elements", []):
        k = e.get("kind")
        if k == "kicker":
            kicker = e.get("text", "")
        elif k == "heading":
            title = e.get("text", "")
        elif k in ("subhead", "statement"):
            sub = e.get("text", "")
        elif k == "insight":
            body.append(f"補足の一言: 「{e.get('text','')}」")
        elif k == "bullets":
            body.append("箇条書き: " + " / ".join(f"「{x}」" for x in e.get("items", [])))
        elif k == "stat":
            d = e.get("delta") or {}
            dd = f"（{'減' if d.get('dir')=='down' else '増'} {d.get('value')}）" if d.get("value") else ""
            body.append(f"中央に非常に大きな数字「{e.get('value','')}{e.get('unit','')}」{dd}、その下に説明「{e.get('caption','')}」")
        elif k == "kpi":
            cards = " / ".join(f"「{t.get('value','')}：{t.get('label','')}」" for t in e.get("tiles", []))
            body.append(f"{len(e.get('tiles',[]))}枚の数字カードを横並び（各カードに小さなアイコン）: {cards}")
        elif k == "columns":
            L, R = e.get("left") or {}, e.get("right") or {}
            body.append(f"左右2カラム対比: 左「{L.get('title','')}」= " + "・".join(L.get("points", []))
                        + f" ／ 右「{R.get('title','')}」= " + "・".join(R.get("points", [])))
        elif k == "features":
            its = " / ".join(f"「{i.get('title','')}: {i.get('body','')}」" for i in e.get("items", []))
            body.append(f"{len(e.get('items',[]))}個の特長カード（各アイコン付き）: {its}")
        elif k == "quote":
            body.append(f"大きな引用「{e.get('text','')}」— {e.get('name','')} {e.get('role','')}")
        elif k == "chart":
            ch = e.get("chart") or {}
            series = "; ".join(f"{s.get('name','')}=[{','.join(str(x) for x in s.get('data',[]))}]" for s in ch.get("series", []))
            body.append(f"{ch.get('kind','column')}グラフ: カテゴリ[{','.join(ch.get('categories',[]))}] 系列 {series}")
        elif k == "table":
            rows = "; ".join(f"{r.get('label','')}=[{','.join(str(c) for c in r.get('cells',[]))}]" for r in e.get("rows", []))
            body.append(f"比較表: 列見出し[{','.join(str(c) for c in e.get('columns',[]))}] 行 {rows}（◯×△は視覚的なチェック/バツで）")
        elif k == "steps":
            st = " → ".join(f"{i+1}.{s.get('title','')}({s.get('desc','')})" for i, s in enumerate(e.get("items", [])))
            body.append(f"左から右へのプロセス図: {st}")
        elif k == "timeline":
            tl = " → ".join(f"{m.get('date','')}:{m.get('label','')}" for m in e.get("items", []))
            body.append(f"横軸のタイムライン: {tl}")
        elif k == "matrix":
            ax, ay = e.get("axis_x") or {}, e.get("axis_y") or {}
            pts = "・".join(f"{i.get('label','')}" for i in e.get("items", []))
            body.append(f"2x2マトリクス: 横軸「{ax.get('low','')}↔{ax.get('high','')}」縦軸「{ay.get('low','')}↔{ay.get('high','')}」に配置: {pts}")
        elif k == "funnel":
            fn = " → ".join(f"{s.get('label','')}({s.get('value','')})" for s in e.get("stages", []))
            body.append(f"上から下へ細くなるファネル図: {fn}")
        elif k == "visual":
            body.append("大きな象徴的ビジュアル＋要点: " + " / ".join(e.get("points", [])))
    return kicker, title, (("サブ: " + sub + "\n") if sub else "") + "\n".join(f"- {b}" for b in body)


def full_prompt(sl: dict, theme: dict, style_override: str | None = None) -> str:
    # style_override(例: 保存済みの画風の自由文)があれば画風を上書き=画風の踏襲
    style = (style_override or "").strip() or VISUAL_STYLE.get(theme.get("name", ""), _DEFAULT_STYLE)
    kicker, title, body = describe_slide(sl)
    is_title = sl.get("layout") in ("cover", "closing", "section")
    return (
        f"# 画風(アートディレクション)\n{style}\n\n"
        f"# 描くスライド（16:9・1枚まるごと・プロのプレゼン資料）\n"
        + (f"左上に小さく章ラベル「{kicker}」。\n" if kicker and not is_title else "")
        + (f"最上部に大きくアクションタイトル（最大2行）「{title}」。\n" if title else "")
        + (f"本文（タイトルを裏付ける図・カード・グラフ。下記の内容をそのまま）:\n{body}\n" if body.strip() else "")
        + "\n# 厳守\n"
        "- 上の数字・固有名・文言は寸分違わず正確に描く。台帳に無い数字・ロゴ・写真・実在人物・グラフの値を勝手に足さない。\n"
        "- 日本語は誤字脱字なく、はっきり読める級数で。1スライド1メッセージ、余白を活かす。\n"
        "- あなたはこのスライドのデザイナーです。上の内容を最も伝わる美しい構成に“デザイン”してください（配置・図解表現・装飾・配色の当て方はお任せ）。"
    )


def _gen_full(prompt: str, size: str):
    key = os.environ["OPENAI_API_KEY"]
    body = json.dumps({"model": MODEL, "prompt": prompt, "size": size, "n": 1, "quality": QUALITY}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/images/generations", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        data = json.load(r)
    return base64.b64decode(data["data"][0]["b64_json"]), (data.get("usage") or {})


def render_deck_ai_full(deck: dict, theme: dict, outdir, cache: bool = True, size: str = FULL_SIZE,
                        style_override: str | None = None) -> dict:
    """ルートA: 各スライドを gpt-image が丸ごとデザイン→そのまま PNG 保存(オーバーレイなし)。
    style_override があれば theme の画風でなくその自由文(保存済みの画風の自由文など)で描く。"""
    outdir = pathlib.Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    generated = 0; usd = 0.0; paths = []
    for sl in deck["slides"]:
        out = outdir / f"{sl['id']}.png"
        if not (cache and out.exists()):
            png, usage = _gen_full(full_prompt(sl, theme, style_override), size)
            out.write_bytes(png); generated += 1
            usd += _cost_from_usage(usage) or COST_PER_IMAGE_USD
        paths.append(out)
    return {"paths": paths, "images_generated": generated,
            "actual_usd": round(usd, 4), "actual_jpy": round(usd * USD_JPY, 1)}


def regen_slides(deck: dict, theme: dict, outdir, ids, size: str = FULL_SIZE) -> dict:
    """指定スライドだけ gpt-image で作り直す(文字照合ゲートで指摘が出た分の修正)。課金あり。"""
    outdir = pathlib.Path(outdir); idset = set(ids); gen = 0; usd = 0.0
    for sl in deck.get("slides", []):
        if sl["id"] not in idset:
            continue
        png, usage = _gen_full(full_prompt(sl, theme), size)
        (outdir / f"{sl['id']}.png").write_bytes(png); gen += 1
        usd += _cost_from_usage(usage) or COST_PER_IMAGE_USD
    return {"images_generated": gen, "actual_usd": round(usd, 4), "actual_jpy": round(usd * USD_JPY, 1)}


def render_deck_ai(deck: dict, theme: dict, outdir, cache: bool = True) -> list:
    """各スライドの AI 背景を生成→正確テキストを重ねて PNG 化。cache=True で既存背景を再利用(再課金回避)。"""
    outdir = pathlib.Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    bgdir = outdir / "ai_bg"; bgdir.mkdir(exist_ok=True)
    subsets = engine.build_font_assets(deck, theme); font_css = engine.font_face_css(subsets)
    base = theme["canvas"]["bg"]; cw, ch = theme["canvas"]["w"], theme["canvas"]["h"]
    from playwright.sync_api import sync_playwright
    paths = []; generated = 0; usd = 0.0
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": cw, "height": ch}, device_scale_factor=2)
        for sl in deck["slides"]:
            bgf = bgdir / f"{sl['id']}.png"
            if not (cache and bgf.exists()):
                png, usage = _gen_bg(bg_prompt(sl.get("layout", ""), theme))
                bgf.write_bytes(png); generated += 1
                usd += _cost_from_usage(usage) or COST_PER_IMAGE_USD
            data_uri = "data:image/png;base64," + base64.b64encode(bgf.read_bytes()).decode()
            role = sl.get("layout", "")
            scrim = _scrim(base, 0.28 if role in ("cover", "closing", "section") else 0.80)
            html = engine.html_for_slide(engine.layout(sl, theme), theme, font_css, bg_data=data_uri, scrim=scrim)
            pg.set_content(html, wait_until="load")
            pg.evaluate("async () => { await Promise.all([...document.fonts].map(f => f.load())); await document.fonts.ready; }")
            pg.evaluate(engine.AUTOFIT_JS)   # 文字切れ根絶(枠に収まるまで縮小)
            out = outdir / f"{sl['id']}.png"; pg.locator(".cv").screenshot(path=str(out)); paths.append(out)
        br.close()
    return {"paths": paths, "images_generated": generated,
            "actual_usd": round(usd, 4), "actual_jpy": round(usd * USD_JPY, 1)}
