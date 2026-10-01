"""slide-studio API (FastAPI)。同一オリジンで web/ を配信するので CORS 不要。

flow: projects → sources(取込) → organize(推奨+構成+盛りすぎチェック) → render(描画) → export(書き出し)
LLM(organize/recommend/factcheck)は選択した Claude/Codex CLI を優先する。engine=ai(gpt-image)は課金ゲートで拒否。
"""
from __future__ import annotations
import logging, pathlib, sys, zipfile
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "api"))
import build as engine
import themes as themes_mod
import store, ingest, organize, recommend, factcheck, design_gate, structures, render_ai, audience, copy_gate, ocr_gate, gap_check, structure_gate, video, llm

log = logging.getLogger("slide-studio")
app = FastAPI(title="slide-studio")


def _combined_text(proj: dict) -> str:
    return "\n\n".join(s["text"] for s in proj["sources"] if s.get("text"))


def _proj_or_404(pid: str) -> dict:
    p = store.load(pid)
    if not p:
        raise HTTPException(404, "project not found")
    return p


def _valid_theme(name: str | None) -> str:
    valid = {t["name"] for t in themes_mod.list_themes()}
    return name if name in valid else themes_mod.DEFAULT_THEME


# ---------------- カタログ ----------------
@app.get("/api/health")
def health():
    available = llm.available_backends()
    try:
        preferred = llm.preferred_backend()
    except RuntimeError:
        preferred = "invalid"
    return {"ok": bool(available), "llm": f"{preferred}-preferred" if available else "none",
            "llm_preferred": preferred, "llm_available": available,
            "llm_ready": bool(available)}

@app.get("/api/themes")
def themes():
    favs = set(store.get_favorites())
    lst = themes_mod.list_themes()
    for i, t in enumerate(lst):
        t["fav"] = t["name"] in favs; t["_o"] = i
    lst.sort(key=lambda t: (not t["fav"], t["_o"]))   # お気に入りを先頭に(相対順は維持)
    for t in lst:
        t.pop("_o", None)
    return lst

@app.get("/api/favorites")
def favorites():
    return store.get_favorites()

@app.post("/api/favorites/{theme}")
def toggle_fav(theme: str):
    if theme not in {t["name"] for t in themes_mod.list_themes()}:
        raise HTTPException(400, "不明な画風です")
    return {"favorites": store.toggle_favorite(theme)}

@app.get("/api/types")
def types():
    return recommend.TYPES

LAYOUT_JA = {"cover": "表紙", "statement": "主張(1枚)", "big-number": "大きな数字", "kpi-row": "KPIカード",
             "two-column": "2カラム対比", "icon-grid": "特長グリッド", "chart": "グラフ", "comparison": "比較表",
             "process": "プロセス図", "timeline": "タイムライン", "quote": "引用", "section": "章の区切り",
             "title-bullets": "箇条書き", "closing": "締め", "matrix": "2x2マトリクス", "funnel": "ファネル",
             "image-lead": "ビジュアル+説明"}

@app.get("/api/layouts")
def layouts():
    return [{"id": k, "name": LAYOUT_JA.get(k, k)} for k in organize.LAYOUTS]


# ---------------- projects ----------------
@app.get("/api/projects")
def list_projects():
    return store.list_all()

@app.post("/api/projects")
def create_project(body: dict = Body(default={})):
    return store.create(body.get("title") or "無題のスライド")

@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _proj_or_404(pid)


# ---------------- sources(1件ずつ取込) ----------------
@app.post("/api/projects/{pid}/sources")
async def add_source(pid: str, kind: str = Form(None), value: str = Form(None),
                     file: UploadFile = File(None)):
    p = _proj_or_404(pid)
    sid = "src_" + __import__("os").urandom(4).hex()
    try:
        if file is not None:
            safe = pathlib.Path(file.filename or "file").name  # ファイル名のパス要素を除去
            dst = store.pdir(pid) / "sources" / f"{sid}_{safe}"
            dst.write_bytes(await file.read())
            text = ingest.from_file(dst); name = safe; kind = "file"
        elif kind == "url" and value:
            text = ingest.from_url(value); name = value
        elif kind == "text" and value:
            text = ingest.from_text(value); name = "貼り付けたテキスト"
        else:
            raise HTTPException(400, "kind/value か file が必要")
    except HTTPException:
        raise
    except Exception as e:
        log.warning("ingest failed: %s", e)
        msg = "このURLは読み込めませんでした。URLを確認してください。" if kind == "url" \
              else "このファイルは読み込めませんでした（暗号化・破損の可能性）。"
        return JSONResponse(status_code=422, content={"error": msg})
    src = {"id": sid, "kind": kind, "name": name, "chars": len(text),
           "text": text, "status": "ok" if text.strip() else "empty"}
    p["sources"].append(src)
    store.save(p)
    return {k: v for k, v in src.items() if k != "text"}

@app.delete("/api/projects/{pid}/sources/{sid}")
def del_source(pid: str, sid: str):
    p = _proj_or_404(pid)
    p["sources"] = [s for s in p["sources"] if s["id"] != sid]
    store.save(p)
    return {"ok": True}


# ---------------- organize(推奨+構成+盛りすぎチェック) ----------------
@app.post("/api/projects/{pid}/organize")
def do_organize(pid: str):
    p = _proj_or_404(pid)
    text = _combined_text(p)
    if not text.strip():
        raise HTTPException(400, "ソースがありません")
    # 1) 宛先・目的を先に推定(構成も言葉づかいもこれに合わせるため)。失敗は致命ではない。
    aud = p.get("audience")
    if not aud:                       # 未設定ならAI推定(UIで利用者が確認・修正できる)
        try:
            aud = audience.propose(text)
        except Exception as e:
            log.warning("audience propose failed: %s", e); aud = dict(audience.DEFAULT)
    rec, structure = None, None
    try:
        rec = recommend.recommend(text, aud)   # 宛先/目的に合わせて構成・画風を選ぶ
        structure = structures.get(rec.get("structure"))
    except Exception as e:
        log.warning("recommend failed: %s", e)
    # 2) 構成生成(章立て背骨=起承転結 + 宛先=言葉づかい)
    try:
        deck = organize.build_deck(p["title"], text, structure=structure, aud=aud)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": f"構成の生成に失敗しました: {e}"})
    # 3) 盛りすぎチェック(事実) + 言葉づかいチェック(宛先) + デザイン採点
    p["deck"] = deck; p["title"] = deck.get("title", p["title"])
    p["recommend"] = rec; p["audience"] = aud
    p["gate"] = factcheck.check(text, deck)
    p["copy_gate"] = copy_gate.check(aud, deck)
    p["gaps"] = gap_check.check(aud, text)   # 目的に対する元ネタの不足要素(捏造せず追記を促す)
    # 構成完全性ゲート: 型の核が無ければ report に自動降格(誤ラベル出荷を防ぐ)
    sg = structure_gate.check(rec.get("structure") if rec else None, deck)
    p["structure_gate"] = sg
    if sg.get("demoted") and rec:
        rec["structure_original"] = rec.get("structure")
        rec["structure"] = sg["effective"]
        rec["structure_display"] = "報告・まとめ(型不成立のため自動降格)"
    p["design"] = design_gate.score(deck)
    store.save(p)
    return p


# ---------------- 宛先・目的(言葉づかいの土台) ----------------
@app.get("/api/audience/options")
def audience_options():
    return {"authors": audience.AUTHORS, "readers": audience.READERS,
            "purposes": audience.PURPOSES, "tones": audience.TONES}

@app.post("/api/projects/{pid}/audience")
def set_audience(pid: str, body: dict = Body(default={})):
    p = _proj_or_404(pid)
    if body.get("propose"):           # AIに再推定させる
        text = _combined_text(p)
        p["audience"] = audience.propose(text) if text.strip() else dict(audience.DEFAULT)
    else:                             # 利用者が指定(部分更新)
        aud = dict(p.get("audience") or audience.DEFAULT)
        for k in ("author", "reader", "purpose", "tone"):
            if str(body.get(k, "")).strip():
                aud[k] = str(body[k]).strip()
        p["audience"] = aud
    store.save(p)
    return {"audience": p["audience"]}


# ---------------- improve(デザイン自己改善) ----------------
@app.post("/api/projects/{pid}/improve")
def do_improve(pid: str):
    p = _proj_or_404(pid)
    if not (p.get("deck") or {}).get("slides"):
        raise HTTPException(400, "先に構成を作ってください")
    text = _combined_text(p)
    try:
        improved, before, after = design_gate.improve(p["deck"], text)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": f"デザイン改善に失敗しました: {e}"})
    p["deck"] = improved; p["design"] = after
    if text.strip():
        p["gate"] = factcheck.check(text, improved)
    store.save(p)
    return {"deck": improved, "design": after, "before": before}


# ---------------- deck 編集 ----------------
@app.patch("/api/projects/{pid}/deck")
def patch_deck(pid: str, deck: dict = Body(...)):
    p = _proj_or_404(pid)
    try:
        clean = organize.sanitize_deck(deck, p["title"])
    except ValueError as e:
        raise HTTPException(400, f"編集内容が不正です: {e}")
    p["deck"] = clean
    text = _combined_text(p)
    if text.strip():
        p["gate"] = factcheck.check(text, clean)   # 編集で内容が変わるのでゲート再実行
    store.save(p)
    return {"deck": clean, "gate": p.get("gate")}


# ---------------- 型切替(1枚を別レイアウトで作り直す) ----------------
@app.post("/api/projects/{pid}/slides/{sid}/relayout")
def relayout(pid: str, sid: str, body: dict = Body(default={})):
    p = _proj_or_404(pid)
    layout = body.get("layout")
    if layout not in organize.LAYOUTS:
        raise HTTPException(400, "不明なレイアウトです")
    deck = p.get("deck") or {}; slides = deck.get("slides", [])
    idx = next((i for i, s in enumerate(slides) if s.get("id") == sid), None)
    if idx is None:
        raise HTTPException(404, "スライドが見つかりません")
    topic = next((e.get("text") for e in slides[idx].get("elements", []) if e.get("kind") == "heading"), "")
    text = _combined_text(p)
    try:
        newslide = organize.build_slide(text, layout, topic)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": f"作り直しに失敗しました: {e}"})
    if not newslide:
        return JSONResponse(status_code=502, content={"error": "このレイアウトで作り直せませんでした"})
    newslide["id"] = sid; slides[idx] = newslide; p["deck"] = deck
    p["design"] = design_gate.score(deck)
    if text.strip():
        p["gate"] = factcheck.check(text, deck)
    store.save(p)
    return {"deck": deck, "design": p["design"], "gate": p.get("gate")}


# ---------------- render(描画) ----------------
@app.post("/api/projects/{pid}/render")
def do_render(pid: str, body: dict = Body(default={})):
    p = _proj_or_404(pid)
    if not (p.get("deck") or {}).get("slides"):
        raise HTTPException(400, "先に構成を作ってください")
    theme_name = _valid_theme(body.get("theme") or (p.get("recommend") or {}).get("theme"))
    ai = body.get("engine") == "ai"
    if ai:
        # 課金ゲート: 承認(approved)無しでは gpt-image を叩かず、概算だけ返す
        est = render_ai.estimate(len((p.get("deck") or {}).get("slides", [])))
        if not body.get("approved"):
            return JSONResponse(status_code=402,
                                content={"error": "AI画像形式は gpt-image の課金が発生します。承認が必要です。",
                                         "estimate": est, "needs_approval": True})
        if not render_ai.key_present():
            return JSONResponse(status_code=400, content={"error": "OPENAI_API_KEY が未設定です(.env を確認)"})
    cost = None
    try:
        deck = organize.sanitize_deck(p["deck"], p["title"])  # 描画前の最終サニタイズ
        if ai:
            th = themes_mod.load(theme_name)
            if body.get("mode") == "hybrid":   # AI背景+正確テキスト
                cost = render_ai.render_deck_ai(deck, th, store.pdir(pid) / "out")
            else:                               # 既定=ルートA(全面委譲)
                cost = render_ai.render_deck_ai_full(deck, th, store.pdir(pid) / "out")
        else:
            engine.render_deck(deck, themes_mod.load(theme_name), store.pdir(pid) / "out")
    except Exception as e:
        log.warning("render failed: %s", e)
        return JSONResponse(status_code=502, content={"error": f"スライドの描画に失敗しました: {e}"})
    slides = [{"id": s["id"], "png_url": f"/files/{pid}/{s['id']}.png"} for s in deck["slides"]]
    p["deck"] = deck
    p["render"] = {"engine": "ai" if ai else "ppt", "theme": theme_name, "slides": slides}
    if cost:
        p["render"]["cost"] = {"images": cost["images_generated"], "usd": cost["actual_usd"], "jpy": cost["actual_jpy"]}
    store.save(p)
    return p["render"]


# ---------------- AI画像の文字照合(vision・課金ゼロ) ----------------
@app.post("/api/projects/{pid}/verify-text")
def verify_text(pid: str):
    p = _proj_or_404(pid)
    if (p.get("render") or {}).get("engine") != "ai":
        raise HTTPException(400, "AI画像形式で描画したあとに使えます")
    try:
        res = ocr_gate.check_deck(p.get("deck") or {}, store.pdir(pid) / "out")
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": f"文字照合に失敗しました: {e}"})
    p["ocr_gate"] = res; store.save(p)
    return res


@app.post("/api/projects/{pid}/fix-text")
def fix_text(pid: str, body: dict = Body(default={})):
    """文字照合ゲートで指摘の出たAI画像スライドだけ gpt-image で作り直す(承認=課金ゲート)。"""
    p = _proj_or_404(pid)
    if (p.get("render") or {}).get("engine") != "ai":
        raise HTTPException(400, "AI画像形式で描画したあとに使えます")
    deck = p.get("deck") or {}; outdir = store.pdir(pid) / "out"
    before = ocr_gate.check_deck(deck, outdir)
    ids = sorted({f["slide"] for f in before.get("flags", [])})
    if not ids:
        return {"fixed": 0, "before": before, "after": before, "cost": None}
    est = render_ai.estimate(len(ids))
    if not body.get("approved"):
        return JSONResponse(status_code=402, content={"error": "指摘スライドを gpt-image で作り直します(課金が発生)。承認が必要です。",
                                                       "slides": ids, "estimate": est, "needs_approval": True})
    if not render_ai.key_present():
        return JSONResponse(status_code=400, content={"error": "OPENAI_API_KEY が未設定です"})
    theme = themes_mod.load(_valid_theme((p.get("render") or {}).get("theme")))
    try:
        cost = render_ai.regen_slides(deck, theme, outdir, ids)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": f"作り直しに失敗しました: {e}"})
    after = ocr_gate.check_deck(deck, outdir)
    p["ocr_gate"] = after; store.save(p)
    return {"fixed": len(ids), "before": before, "after": after,
            "cost": {"images": cost["images_generated"], "jpy": cost["actual_jpy"]}}


# ---------------- 動画化(ナレーション付き・ローカル/課金ゼロ) ----------------
@app.post("/api/projects/{pid}/video")
def make_video(pid: str, body: dict = Body(default={})):
    p = _proj_or_404(pid)
    if not p.get("render"):
        raise HTTPException(400, "先に描画してください")
    if not video.vv_available():
        return JSONResponse(status_code=400, content={"error": "音声エンジン(VOICEVOX)が起動していません。起動してからお試しください。"})
    deck = p.get("deck") or {}
    try:
        narr = video.narration(deck, p.get("audience") or {})
        res = video.build_video(deck, store.pdir(pid) / "out", narr, speaker=video.resolve_speaker(body.get("speaker")))
    except Exception as e:
        log.warning("video failed: %s", e)
        return JSONResponse(status_code=502, content={"error": f"動画の作成に失敗しました: {e}"})
    p["video"] = {"url": f"/files/{pid}/deck.mp4", "seconds": res["seconds"], "slides": res["slides"]}
    p["narration"] = narr
    store.save(p)
    return p["video"]


# ---------------- export(書き出し) ----------------
@app.post("/api/projects/{pid}/export")
def do_export(pid: str, body: dict = Body(default={})):
    p = _proj_or_404(pid)
    if not p.get("render"):
        raise HTTPException(400, "先に描画してください")
    fmt = body.get("format", "pptx")
    deck = p["deck"]
    theme = themes_mod.load(_valid_theme(p["render"].get("theme")))
    outdir = store.pdir(pid) / "out"
    # deck の順序で明示収集(glob辞書順による欠落/順序破壊を回避)
    pngs = [outdir / f"{s['id']}.png" for s in deck["slides"]]
    pngs = [q for q in pngs if q.exists()]
    if not pngs:
        raise HTTPException(400, "描画されたページがありません。もう一度描画してください")
    try:
        if fmt == "png":
            out = outdir / "deck_png.zip"
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
                for i, png in enumerate(pngs, 1):
                    zf.write(png, f"{i:02d}_{png.name}")
        elif fmt == "pdf":
            out = engine.export_pdf(pngs, outdir / "deck.pdf")
        elif fmt == "pptx":
            if (p.get("render") or {}).get("engine") == "ai":   # AI画像形式=画像貼りPPTX
                out = render_ai.export_pptx_images(pngs, outdir / "deck.pptx")
            else:
                subsets = engine.build_font_assets(deck, theme)
                engine.render_pptx(deck, theme, outdir / "deck.pptx", subsets)
                out = outdir / "deck.pptx"
        else:
            raise HTTPException(400, "format は png/pdf/pptx")
    except HTTPException:
        raise
    except Exception as e:
        log.warning("export %s failed: %s", fmt, e)
        return JSONResponse(status_code=502, content={"error": f"書き出しに失敗しました: {e}"})
    return {"url": f"/files/{pid}/{out.name}", "format": fmt}


# ---------------- ファイル配信(封じ込め検証つき) ----------------
@app.get("/files/{pid}/{fname}")
def get_file(pid: str, fname: str):
    base = (store.pdir(pid) / "out").resolve()
    f = (base / pathlib.Path(fname).name).resolve()
    if not (f == base or base in f.parents) or not f.exists():
        raise HTTPException(404, "not found")
    return FileResponse(str(f))


# ---------------- フロント(web/) ----------------
WEB = ROOT / "web"
if WEB.exists():
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
