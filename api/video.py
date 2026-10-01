"""完成スライド束 → ナレーション付き動画(MP4)。
台本=claude(課金ゼロ) / 音声=VOICEVOX(ローカル:50021・無料) / 合成=ffmpeg。全てローカルで課金ゼロ。

各スライドPNGを、そのスライドのナレーション音声の長さぶん表示し、順に連結する。
"""
from __future__ import annotations
import json, subprocess, pathlib, urllib.request, urllib.parse, wave, contextlib
import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "engine"))
import llm
import render_ai   # describe_slide を流用(スライド内容の要約)

import os
VV = os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021")


def resolve_speaker(value=None) -> int:
    """VOICEVOX の話者 ID を決める。リクエストの値を優先し、無ければ環境変数 VOICEVOX_SPEAKER_ID。
    どちらも無い場合はコードに既定値を持たず、エラーにする。"""
    raw = value if value not in (None, "") else os.environ.get("VOICEVOX_SPEAKER_ID", "")
    if raw in (None, ""):
        raise RuntimeError("VOICEVOX の話者 ID が未設定です。環境変数 VOICEVOX_SPEAKER_ID を設定するか、リクエストで speaker を指定してください")
    return int(raw)


W, H = 1280, 720


def vv_available() -> bool:
    try:
        urllib.request.urlopen(VV + "/version", timeout=2); return True
    except Exception:
        return False


def narration(deck: dict, aud: dict) -> dict:
    """各スライドの話し言葉ナレーションを1回のclaudeで生成。戻り: {slide_id: text}"""
    reader = (aud or {}).get("reader", "聞き手"); tone = (aud or {}).get("tone", "")
    blocks = []
    for sl in deck.get("slides", []):
        k, t, body = render_ai.describe_slide(sl)
        blocks.append(f"[{sl['id']}] 見出し:{t}｜{body[:200]}")
    prompt = f"""この資料を{reader}に向けて話す解説動画のナレーション台本を書いてください。
- 各スライド1〜3文、自然な話し言葉。トーン「{tone}」。
- スライドの文字を棒読みせず要点を語る。数値・固有名は正確に。1枚は2〜5秒で話せる長さ。
## スライド
{chr(10).join(blocks)}
## 出力(JSONのみ・キーはスライドID)
{{"{deck['slides'][0]['id']}":"ナレーション文", ...}}
"""
    try:
        r = llm.generate_json(prompt, "あなたは解説動画のナレーターです。自然な話し言葉で、正確に。JSONのみ。")
        return {str(k): str(v) for k, v in r.items()} if isinstance(r, dict) else {}
    except Exception:
        return {}


def _synth(text: str, speaker: int) -> bytes:
    q = urllib.request.urlopen(urllib.request.Request(
        f"{VV}/audio_query?text={urllib.parse.quote(text)}&speaker={speaker}", method="POST"), timeout=30).read()
    return urllib.request.urlopen(urllib.request.Request(
        f"{VV}/synthesis?speaker={speaker}", data=q, headers={"Content-Type": "application/json"}, method="POST"), timeout=90).read()


def _wav_dur(path) -> float:
    with contextlib.closing(wave.open(str(path), "rb")) as w:
        return w.getnframes() / float(w.getframerate())


def _silence(path, sec: float):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono", "-t", f"{sec}",
                    "-c:a", "pcm_s16le", str(path)], check=True, capture_output=True)


def build_video(deck: dict, outdir, narr: dict, speaker: int | None = None) -> dict:
    """スライドPNG(outdir/<id>.png)+ナレーションから MP4 を作る。戻り: {path, seconds, slides}"""
    speaker = resolve_speaker(speaker)
    outdir = pathlib.Path(outdir); vdir = outdir / "video"; vdir.mkdir(parents=True, exist_ok=True)
    segs = []; total = 0.0
    for i, sl in enumerate(deck.get("slides", [])):
        png = outdir / f"{sl['id']}.png"
        if not png.exists():
            continue
        wav = vdir / f"{sl['id']}.wav"; text = (narr.get(sl["id"]) or "").strip()
        if text:
            wav.write_bytes(_synth(text, speaker))
        else:
            _silence(wav, 3.0)
        dur = max(1.5, _wav_dur(wav) + 0.6)   # 末尾に少し余白
        total += dur
        seg = vdir / f"seg_{i:02d}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", str(png), "-i", str(wav),
                        "-c:v", "libx264", "-tune", "stillimage", "-c:a", "aac", "-b:a", "192k",
                        "-pix_fmt", "yuv420p", "-vf", f"scale={W}:{H}", "-t", f"{dur}", str(seg)],
                       check=True, capture_output=True)
        segs.append(seg)
    if not segs:
        raise RuntimeError("動画にできるスライドがありません")
    lst = vdir / "list.txt"
    lst.write_text("".join(f"file '{s.name}'\n" for s in segs))
    out = outdir / "deck.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)],
                   check=True, capture_output=True, cwd=str(vdir))
    return {"path": out, "seconds": round(total, 1), "slides": len(segs)}
