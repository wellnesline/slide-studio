"""自己完結の .env ローダ(親リポジトリ非依存・配布可能)。

探索順: slide-studio/.env → その親ディレクトリを上へ辿って最初に見つかった .env。
シェルに空/ダミーのキーが居座っていても、.env の非空値で上書きする
(既存環境変数が非空でも、_OVERWRITE のキーは .env 側を優先=キー居座り事故の回避)。
"""
from __future__ import annotations
import os
import pathlib

# .env の非空値が 既存環境変数を上書きしてよいキー(APIキーの居座り事故対策)
_OVERWRITE = {"OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ANTHROPIC_API_KEY"}


def load_env() -> None:
    here = pathlib.Path(__file__).resolve().parents[1]   # slide-studio/
    candidates = [here / ".env"] + [p / ".env" for p in here.parents]
    env_file = next((p for p in candidates if p.exists()), None)
    if not env_file:
        return
    try:
        lines = env_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if not v:
            continue
        if k in _OVERWRITE or not os.environ.get(k, "").strip():
            os.environ[k] = v
