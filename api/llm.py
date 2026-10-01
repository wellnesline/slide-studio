"""LLM呼び出し(codex / claude CLI・ローカルでログイン済みの認証・APIキー不要)。deckgen の方式を踏襲。

organize(ソース→スライド構成) と recommend(画風×型推奨) に使う。gpt-image(課金)は使わない。
"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import base64, json, mimetypes, os, re, shutil, subprocess, tempfile, time

CLI_TIMEOUT = 240
CODEX_TIMEOUT = 300
_TRACE_PATH: ContextVar[str | None] = ContextVar("llm_trace_path", default=None)


@contextmanager
def record_calls(log_path: str | os.PathLike | None):
    """LLM 呼び出しの非機密メタデータだけを JSONL に記録する。未指定時は no-op。"""
    if not log_path:
        yield
        return
    path = os.fspath(log_path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    token = _TRACE_PATH.set(path)
    try:
        yield
    finally:
        _TRACE_PATH.reset(token)


def _prompt_kind(prompt: str) -> str:
    # プロンプト本文を trace に含めない。既知の固定見出しだけを種別へ写像する。
    if "# 記事本文" in prompt and "contract_version" in prompt:
        return "jargon_audit"
    if "# 重要事実チェックリスト" in prompt:
        return "important_facts_audit"
    if "# 固定アウトライン" in prompt:
        return "structure_diagnosis"
    if "# 対象記事の一文" in prompt:
        return "jargon_revision"
    if "# テキストの塊" in prompt:
        return "faithful_chunk"
    return "generic"


def _trace_call(start: float, backend: str, success: bool, retry: int, prompt: str,
                error: Exception | None = None) -> None:
    path = _TRACE_PATH.get()
    if not path:
        return
    try:
        ended = time.time()
        event = {"started_at": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                 "ended_at": datetime.fromtimestamp(ended, timezone.utc).isoformat(),
                 "elapsed_seconds": round(ended - start, 6), "backend": backend,
                 "success": success, "retry": retry, "prompt_kind": _prompt_kind(prompt)}
        if error is not None:
            event["error_type"] = type(error).__name__
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass  # trace の失敗で本来の生成を失敗させない


def _resolve_cli(name: str) -> str | None:
    """CLI の実行ファイルを絶対パスで解決する。

    Windows では codex / claude が `.cmd` として入る。 存在確認だけ shutil.which で行い
    実行は裸の名前を渡す書き方だと、 which が見つけているのに起動が WinError 2 で失敗する
    (実測: `subprocess.run(["codex","--version"])` は WinError 2、
     which の返す絶対パスなら rc=0)。 存在確認と実行で必ず同じ解決結果を使う。

    `CODEX_BIN` / `CLAUDE_BIN` で明示指定も可能 (backend の claude_cli.py と同じ規約)。
    """
    override = os.environ.get(f"{name.upper()}_BIN", "").strip()
    if override:
        return override if os.path.exists(override) else None
    return shutil.which(name)


def cli_available() -> bool:
    return _resolve_cli("claude") is not None


def codex_available() -> bool:
    return _resolve_cli("codex") is not None


# 従量課金の API へ黙って切り替えないための防波堤。
# このモジュールは手元でログイン済みの CLI (claude / codex) で動くことを前提にしており、
# 画像や音声の承認ゲートより前に文章生成が走る。 そのため CLI が見つからないときに
# 黙って従量課金の Anthropic API へ切り替わると、 ユーザーが一度も承認していない
# 課金が発生しうる。 API を使うには明示的な opt-in を必須にする。
PAID_API_OPT_IN = "STUDIO_ALLOW_PAID_ANTHROPIC_API"


def paid_api_allowed() -> bool:
    return os.environ.get(PAID_API_OPT_IN, "").strip().lower() in ("1", "true", "yes", "on")


def available_backends() -> list[str]:
    out = []
    # キーがあるだけでは「使える」と報告しない。 opt-in が無いと下で拒否されるため、
    # ここで available と返すと 「使えるはずなのに失敗する」 ことになる。
    if cli_available() or (os.environ.get("ANTHROPIC_API_KEY") and paid_api_allowed()):
        out.append("claude")
    if codex_available():
        out.append("codex")
    return out


def preferred_backend(explicit: str = "") -> str:
    requested = (explicit or os.environ.get("STUDIO_LLM_BACKEND") or "").strip().lower()
    if requested:
        if requested not in ("claude", "codex"):
            raise RuntimeError(f"不明な LLM backend: {requested} (claude または codex を指定)")
        return requested
    return "codex" if codex_available() else "claude"


def _generate_codex(full_prompt: str, model: str, effort: str,
                    images: list[str] | None = None) -> str:
    """Codex CLI の agent_message を一時出力ファイルから取得する。"""
    output_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="studio-codex-", suffix=".txt", delete=False) as f:
            output_path = f.name
        env = os.environ.copy()
        env.pop("OPENAI_API_KEY", None)  # API key mode を絶対に継承しない
        env.pop("CODEX_API_KEY", None)
        for key in ("CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_CI"):
            env.pop(key, None)
        timeout = int(os.environ.get("STUDIO_CODEX_TIMEOUT", str(CODEX_TIMEOUT)))
        codex_bin = _resolve_cli("codex")
        if not codex_bin:
            raise RuntimeError("codex CLI が見つかりません(PATH または CODEX_BIN を確認)")
        cmd = [codex_bin, "exec", "-", "--skip-git-repo-check", "--sandbox", "read-only",
               "-m", model, "-c", f"model_reasoning_effort={effort}", "-o", output_path]
        for image in images or []:
            cmd += ["-i", image]
        r = subprocess.run(cmd, input=full_prompt, text=True, encoding="utf-8",
                           capture_output=True, timeout=timeout, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"codex CLI error (rc={r.returncode}): {(r.stderr or '')[:300]}")
        with open(output_path, encoding="utf-8") as f:
            result = f.read().strip()
        if not result:
            raise RuntimeError("codex CLI error: agent_message が空です")
        return result
    finally:
        if output_path:
            try:
                os.unlink(output_path)
            except FileNotFoundError:
                pass


def _generate_claude(full_prompt: str, model: str, max_tokens: int,
                     images: list[str] | None = None, *, system: str = "",
                     user_prompt: str = "") -> tuple[str, str]:
    if cli_available():
        workspace = None
        try:
            prompt = full_prompt
            if images:
                workspace = tempfile.mkdtemp(prefix="slide-studio-vision-")
                names = []
                for index, source in enumerate(images):
                    suffix = os.path.splitext(source)[1] or ".png"
                    name = f"image_{index}{suffix}"
                    shutil.copyfile(source, os.path.join(workspace, name))
                    names.append(name)
                prompt += "".join(f" @{name}" for name in names)
            claude_bin = _resolve_cli("claude")
            if not claude_bin:
                raise RuntimeError("claude CLI が見つかりません(PATH または CLAUDE_BIN を確認)")
            cmd = [claude_bin, "-p", "--output-format", "text"]
            if model:
                cmd += ["--model", model]
            env = os.environ.copy()
            env.pop("ANTHROPIC_API_KEY", None)  # CLI のログイン認証を API key mode と混在させない
            r = subprocess.run(cmd, input=prompt, text=True, encoding="utf-8",
                               capture_output=True, timeout=CLI_TIMEOUT, cwd=workspace or None,
                               env=env)
            if r.returncode != 0:
                raise RuntimeError(f"claude CLI error (rc={r.returncode}): {(r.stderr or '')[:300]}")
            result = (r.stdout or "").strip()
            if not result:
                raise RuntimeError("claude CLI error: 応答が空です")
            return result, "claude_cli"
        finally:
            if workspace:
                shutil.rmtree(workspace, ignore_errors=True)
    if os.environ.get("ANTHROPIC_API_KEY"):
        if not paid_api_allowed():
            raise RuntimeError(
                "claude CLI が見つかりません。 ANTHROPIC_API_KEY は設定されていますが、"
                "従量課金の Anthropic API へ自動では切り替えません。"
                f" API を使う場合は {PAID_API_OPT_IN}=1 を明示してください。"
            )
        import anthropic
        c = anthropic.Anthropic()
        content = user_prompt or full_prompt
        if images:
            blocks = [{"type": "text", "text": content}]
            for image in images:
                media_type = mimetypes.guess_type(image)[0] or "image/png"
                with open(image, "rb") as image_file:
                    data = base64.b64encode(image_file.read()).decode("ascii")
                blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                })
            content = blocks
        m = c.messages.create(model="claude-sonnet-4-5", max_tokens=max_tokens,
                              system=system or "",
                              messages=[{"role": "user", "content": content}])
        result = "".join(b.text for b in m.content if getattr(b, "type", "") == "text").strip()
        if not result:
            raise RuntimeError("Anthropic API error: 応答が空です")
        return result, "anthropic_api"
    raise RuntimeError(
        "claude CLI が見つかりません(PATH または CLAUDE_BIN を確認)。"
        f" 従量課金 API を使う場合は ANTHROPIC_API_KEY と {PAID_API_OPT_IN}=1 の両方が必要です。"
    )


def generate(prompt: str, system: str = "", model: str = "", max_tokens: int = 3000,
             *, backend: str = "", images: list[str] | None = None) -> str:
    # model="" のときは --model を付けない=ユーザーのclaude CLI設定モデルに従う(studio-agents方針)
    started = time.time()
    full = (system.strip() + "\n\n" + prompt) if system else prompt
    selected_backend = preferred_backend(backend)
    order = [selected_backend, "claude" if selected_backend == "codex" else "codex"]
    errors = []
    for attempt, candidate in enumerate(order):
        try:
            if candidate == "codex":
                if not codex_available():
                    raise RuntimeError("codex CLI が見つかりません")
                codex_model = (os.environ.get("STUDIO_CODEX_MODEL") or
                               (model if model.startswith("gpt-") else "") or "gpt-5.6-terra")
                codex_effort = os.environ.get("STUDIO_CODEX_EFFORT") or "low"
                result = _generate_codex(full, codex_model, codex_effort, images)
                trace_backend = "codex_cli"
            else:
                claude_model = "" if model.startswith("gpt-") else model
                result, trace_backend = _generate_claude(
                    full, claude_model, max_tokens, images,
                    system=system, user_prompt=prompt,
                )
            _trace_call(started, trace_backend, True, attempt, prompt)
            return result
        except Exception as e:
            errors.append(f"{candidate}: {e}")
            _trace_call(started, f"{candidate}_cli", False, attempt, prompt, e)
    error = RuntimeError("LLM backend が両方失敗しました: " + " | ".join(errors))
    _trace_call(started, "none", False, 0, prompt, error)
    raise error


def generate_json(prompt: str, system: str = "", model: str = "", *, backend: str = "") -> dict:
    txt = generate(prompt + "\n\n出力はJSONのみ。前後に地の文・```を付けない。", system, model,
                   backend=backend)
    return extract_json(txt)


def extract_json(text: str):
    if not text:
        return {}
    # ```json ... ``` を剥がす
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    m = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        # 末尾カンマ等の軽微な崩れを緩和
        s = re.sub(r",(\s*[}\]])", r"\1", m.group(0))
        return json.loads(s)
