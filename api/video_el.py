"""完成デッキ → ElevenLabs ナレーション付き動画(MP4)。

動画バックエンドの seminar 一気通貫(build_episode.run)を薄ラッパで呼ぶ(方式B・A'案 構造reveal)。
- 音声 = ElevenLabs 一本化。ELキー無ければこの機能は使えない(冒頭で必須チェック→即エラー)。
- スライド = slide-studio が描いた PNG を注入(build は生成 skip = 二重生成なし)。
- パワポ型は構造化PPTX を渡し、動画バックエンド側で svg_reveal(構造reveal)。
- 音声プリセット4種は presets/voice/*.yaml(声=実EL voice_id / ナレ口調=架空 persona)。

課金: EL は従量課金。build 前に概算を出し、承認後にのみ実行する(金額感は都度提示)。
"""
from __future__ import annotations
import json, os, pathlib, signal, subprocess, sys, uuid
import yaml

import build as engine
import themes as themes_mod
import render_ai
import llm
import store
from _env import load_env as _load_env
_load_env()   # ELEVENLABS_API_KEY 等を .env から(自己完結・配布先でも動く)

ROOT = pathlib.Path(__file__).resolve().parents[1]
PRESETS_DIR = ROOT / "presets" / "voice"


def looks_like_backend(path: pathlib.Path) -> bool:
    """解決先が実際に動画バックエンド（非同梱）かを確かめる。

    存在するだけの同名ディレクトリを掴むと、 後段で原因の分かりにくいエラーになる。
    """
    return (path / "api").is_dir() and (path / "tools" / "slidestudio_build.py").exists()


def resolve_backend_dir() -> pathlib.Path:
    """環境変数、続いて隣接配置の順で backend を解決する。

    ★ studio-agents/core/backend_path.py の同名関数と探索順を必ず揃えること。
      片方だけ変えると、 studio-agents の CLI と描画側が別の backend を掴む。
    """
    configured = os.environ.get("VIDEO_BACKEND_DIR", "").strip()
    if configured:
        # 明示指定は利用者の意思なので、 中身が backend に見えなくても尊重する。
        return pathlib.Path(configured).expanduser().resolve()

    distributed = ROOT.parent / "video-backend" / "backend"
    if looks_like_backend(distributed):
        return distributed.resolve()

    return distributed.resolve()


VIDEO_BACKEND = resolve_backend_dir()

# EL 概算単価(¥/1000字・概算表示用。実課金は EL 側実測)。保守的レンジで提示。
_EL_YEN_PER_1000 = 4.5


def el_key_present() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())


def _valid_theme(name: str | None) -> str:
    valid = {t["name"] for t in themes_mod.list_themes()}
    return name if name in valid else themes_mod.DEFAULT_THEME


def list_presets() -> list[dict]:
    """4音声プリセットを読み込む(id/label/gender)。"""
    out = []
    for f in sorted(PRESETS_DIR.glob("*.yaml")):
        try:
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if d.get("id") and d.get("voice_id"):
            out.append(d)
    return out


def _load_preset(preset_id: str) -> dict:
    for d in list_presets():
        if d["id"] == preset_id:
            return d
    raise ValueError(f"不明な音声プリセット: {preset_id}")


def narrate_el(deck: dict, aud: dict, persona: dict, tone: str) -> dict:
    """各スライドの話し言葉ナレを1回の claude で生成(persona 口調で・課金ゼロ)。戻り {slide_id: text}。"""
    reader = (aud or {}).get("reader", "聞き手")
    fp = (persona or {}).get("first_person", "私")
    reg = (persona or {}).get("register", "です・ます調")
    pers = (persona or {}).get("personality", "")
    blocks = []
    for sl in deck.get("slides", []):
        k, t, body = render_ai.describe_slide(sl)
        blocks.append(f"[{sl['id']}] 見出し:{t}｜{body[:200]}")
    prompt = f"""この資料を{reader}に向けて話す解説動画のナレーション台本を書いてください。
- 話者の一人称は「{fp}」、口調は「{reg}」、トーンは「{tone}」。話者の人物像: {pers}
- 各スライド1〜3文、自然な話し言葉。スライドの文字を棒読みせず要点を語る。数値・固有名は正確に。1枚2〜5秒。
## スライド
{chr(10).join(blocks)}
## 出力(JSONのみ・キーはスライドID)
{{"{deck['slides'][0]['id']}":"ナレーション文", ...}}
"""
    try:
        r = llm.generate_json(prompt, f"あなたは解説動画のナレーターです。{reg}・{tone}。自然な話し言葉で正確に。JSONのみ。")
        return {str(k): str(v) for k, v in r.items()} if isinstance(r, dict) else {}
    except Exception:
        return {}


def estimate_cost(narr: dict) -> dict:
    chars = sum(len((v or "").strip()) for v in narr.values())
    yen = round(chars / 1000.0 * _EL_YEN_PER_1000, 1)
    return {"chars": chars, "yen_approx": yen, "note": "ElevenLabs 従量課金の概算(実額はEL側実測)"}


def _render_assets(pid: str, deck: dict, theme_name: str, engine_kind: str = "ppt") -> tuple[list[pathlib.Path], pathlib.Path | None]:
    """スライド画像を out/ に描画。戻り (png_paths, pptx_path|None)。

    engine_kind:
      "ppt" = テンプレ描画(HTML/chromium・課金ゼロ)＋ 構造化PPTX(svg構造reveal元)。
      "ai"  = gpt-image フル生成(2048×1152・従量課金)。1枚絵なのでPPTX無し → runner は CV blob reveal。
    """
    theme = themes_mod.load(theme_name)
    outdir = store.pdir(pid) / "out"
    if engine_kind == "ai":
        # gpt-image は cache=True で既存PNGを再利用(再課金しない)。承認は呼び出し側で担保。
        res = render_ai.render_deck_ai_full(deck, theme, outdir, cache=True, size="2048x1152")
        return res["paths"], None
    res = engine.render_deck(deck, theme, outdir)          # PNG(2560x1440) + subsets
    pngs = res["pngs"]
    pptx = outdir / "deck.pptx"
    engine.render_pptx(deck, theme, pptx, res["subsets"])  # 実テキストボックスPPTX(構造reveal元)
    return pngs, pptx


def build_el_video(pid: str, preset_id: str, *, approved: bool = False, engine_kind: str = "ppt") -> dict:
    """デッキ → EL動画。approved=False なら概算だけ返し build しない(課金ゲート)。

    engine_kind="ppt"(テンプレ・課金ゼロ) / "ai"(gpt-image・従量課金・見栄え優先)。
    戻り: 成功時 {"ok":True,"path":..,"seconds":..,"preset":..,"cost":..}
          未承認 {"ok":False,"needs_approval":True,"cost":..}
          失敗   {"ok":False,"error":..}
    """
    if not el_key_present():
        return {"ok": False, "error": "この機能は ElevenLabs の API キー(ELEVENLABS_API_KEY)が必要です。"}
    if engine_kind == "ai" and not render_ai.key_present():
        return {"ok": False, "error": "gpt-image 形式は OPENAI_API_KEY が必要です。"}
    p = store.load(pid)
    if not p:
        return {"ok": False, "error": "プロジェクトが見つかりません"}
    deck = p.get("deck") or {}
    if not deck.get("slides"):
        return {"ok": False, "error": "先に構成を作ってください"}
    preset = _load_preset(preset_id)
    theme_name = (p.get("render") or {}).get("theme") or (p.get("recommend") or {}).get("theme")
    theme_name = _valid_theme(theme_name)

    # 1. ナレ生成(persona 口調・課金ゼロ)
    narr = narrate_el(deck, p.get("audience") or {}, preset.get("narration_persona") or {},
                      preset.get("narration_tone", ""))

    # 2. 課金ゲート: 未承認なら概算だけ返す
    cost = estimate_cost(narr)
    if engine_kind == "ai":
        # ai は下の _render_assets で gpt-image を全枚数生成する = 課金される。
        # EL 分だけの概算を見せると、承認した額より実額が大きくなる。
        img = render_ai.estimate(len(deck["slides"]))
        cost = {
            **cost,
            "images": img["images"],
            "images_yen_approx": img["jpy"],
            "el_yen_approx": cost["yen_approx"],
            "yen_approx": round(cost["yen_approx"] + img["jpy"], 1),
            "note": "ElevenLabs 音声 + gpt-image 画像の合算概算(実額は各サービス実測)",
        }
    if not approved:
        return {"ok": False, "needs_approval": True, "cost": cost, "preset": preset.get("label", preset_id)}

    # 3. スライド画像を描画(ppt=課金ゼロ / ai=gpt-image従量課金)
    pngs, pptx = _render_assets(pid, deck, theme_name, engine_kind)

    # 4. job.json を組み、動画バックエンド runner を起動
    cid = f"ss_{pid}"
    vid = f"v_{uuid.uuid4().hex[:8]}"
    png_by_id = {p2.stem: p2 for p2 in pngs}
    slides = []
    for sl in deck["slides"]:
        sid = sl["id"]
        png = png_by_id.get(sid)
        if png is None:
            continue
        slides.append({"id": sid, "png": str(png.resolve()), "narration": narr.get(sid, "")})
    job = {
        "cid": cid, "vid": vid, "title": deck.get("title", p.get("title", "スライド解説")),
        "preset": {
            "id": preset["id"], "label": preset.get("label", preset["id"]),
            "voice_id": preset["voice_id"], "gender": preset.get("gender", ""),
            "seed": preset.get("seed", 42), "voice_params": preset.get("voice_params") or {},
        },
        "slides": slides,
        "pptx": str(pptx.resolve()) if (pptx and pptx.exists()) else None,
    }
    job_path = store.pdir(pid) / "out" / "el_job.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")

    final = _run_runner(job_path)
    if not final:
        return {"ok": False, "error": "動画ビルドに失敗しました(動画バックエンド runner)。ログを確認してください。", "cost": cost}
    # final.mp4 を out/ に回収
    dest = store.pdir(pid) / "out" / "deck_el.mp4"
    import shutil
    shutil.copy(final, dest)
    return {"ok": True, "path": str(dest), "preset": preset.get("label", preset_id), "cost": cost}


def _terminate_tree(proc: subprocess.Popen) -> None:
    """直接の子だけでなく、その先の孫まで止める。

    subprocess の timeout は直接の子しか kill しない。 ここでの子は uv で、
    その先に python → ffmpeg / TTS / 画像生成 が連なるため、 子だけ殺すと
    孫が生き残る。 生き残った孫は処理を続け、 従量課金も走り続けうる。
    """
    try:
        if os.name == "nt":
            # taskkill /T がプロセスツリーごと落とす。 Job Object を組むより確実で副作用が小さい。
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, check=False)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _run_runner(job_path: pathlib.Path) -> pathlib.Path | None:
    """動画バックエンド（非同梱）の runner を uv 経由で起動。標準出力末尾の `FINAL <path>` を拾う。"""
    runner = VIDEO_BACKEND / "tools" / "slidestudio_build.py"
    if not runner.exists():
        return None
    # 子は cwd=VIDEO_BACKEND で動く。 相対のまま渡すと backend 配下を探しにいくので、
    # 親が作った job を指さなくなる (例: out/el_job.json → backend/out/el_job.json)。
    job_arg = str(job_path.resolve())
    # 子の標準出力を UTF-8 に固定する。 下の encoding="utf-8" は「親がどう復号するか」で
    # あって子の出力設定ではないため、 日本語 Windows の子は既定 cp932 で書き出す。
    # ずれると FINAL 行の日本語パスが壊れ、 errors="replace" がそれを隠してしまう。
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # `uv run` は既定で backend の venv を同期する。 通常利用ではそれが正しいが、
    # 検証や POC で稼働中の venv に触れたくない場合の逃げ道を用意する
    # (既定は従来どおり。 依存が揃っていないと動かないため opt-in にする)。
    cmd = ["uv", "run"]
    if os.environ.get("VIDEO_RUNNER_NO_SYNC", "").strip().lower() in ("1", "true", "yes", "on"):
        cmd.append("--no-sync")
    cmd += ["python", "tools/slidestudio_build.py", job_arg]
    # POSIX は新しいセッションにして killpg でまとめて落とせるようにする。
    popen_kw = {} if os.name == "nt" else {"start_new_session": True}
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(VIDEO_BACKEND), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=env, **popen_kw,
        )
    except OSError:
        return None
    try:
        stdout, stderr = proc.communicate(timeout=1800)
    except subprocess.TimeoutExpired:
        # 子だけ殺すと孫が残り、処理と課金が続く。ツリーごと止めてから回収する。
        _terminate_tree(proc)
        stdout, stderr = proc.communicate()
        sys.stderr.write("動画バックエンド runner がタイムアウトしました(子孫プロセスを停止しました)\n")
        sys.stderr.write((stderr or "")[-2000:])
        return None
    if proc.returncode != 0:
        # 異常終了。 前回実行の残骸など、 出力に残っていた FINAL 行を成功と誤認しない。
        sys.stderr.write((stderr or "")[-2000:])
        return None
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("FINAL "):
            fp = pathlib.Path(line[len("FINAL "):].strip())
            return fp if fp.exists() else None
    # 失敗時は stderr 末尾を stdout に出してデバッグ可能に
    sys.stderr.write((stderr or "")[-2000:])
    return None
