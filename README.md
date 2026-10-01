# slide-studio

Source-grounded slide deck generator: turns documents/URLs into an editable PPTX and PNG deck, with checks against over-claiming.

資料・URL・テキストから、編集可能な PPTX と PNG のスライドを作る FastAPI アプリとレンダリングエンジンです。公開用に整理したサンプル実装です。

## 概要

スライド生成でよくある問題は、元資料に無い数字や固有名詞を AI が足してしまうこと、箇条書きだらけで単調になること、PNG と PPTX で見た目が食い違うことです。このリポジトリは次の方針でそれを抑えています。

- 本文は取り込んだソースのテキストだけを根拠にする（捏造しない）
- 生成後にゲートで点検し、問題があれば指摘または自己改善する
- 同じレイアウト計算から PNG と PPTX の両方を出し、見た目を揃える

## 主な機能・設計のポイント

- **フロー**: プロジェクト作成 → ソース取り込み（URL / PDF / PPTX / DOCX / テキスト）→ 構成（organize）→ 描画 → 書き出し。`api/main.py` が REST API と `web/` の画面を同一オリジンで配信します
- **画風 × 構成の推奨**: `recommend.py` が元ネタから画風（`themes/*.yaml`）と話の型（`structures.py`）を理由付きで提案。構成は内容に合ったコンポーネント（数値は big-number / chart、対照は 2 カラム、など）を選びます
- **品質ゲート（`api/` 配下）**
  - `factcheck.py`: 元ネタで裏付けられない具体的事実（数値・割合・日付・固有名詞）を検出
  - `copy_gate.py`: 宛先（`audience.py`）に照らして言葉づかいを点検
  - `gap_check.py`: 目的に対して元ネタに足りない要素を指摘（捏造で埋めない）
  - `structure_gate.py`: 宣言した型の核になる要素が実在しなければ、型ラベルを降格
  - `design_gate.py`: 多様性・ビジュアル比率・箇条書き比率で採点し、低ければ改善
  - `ocr_gate.py`: AI 画像に焼き込まれた文字が期待どおりかを vision で照合
- **PNG / PPTX parity**: `engine/build.py` が同一の座標計算から HTML→PNG（Playwright）と python-pptx を出力。使う文字だけにサブセット化した同一フォントを両方に埋め込みます（`engine/fonts.py`）。アイコンは Lucide をインライン化（`engine/icons.py`）
- **AI 画像はハイブリッド**: `render_ai.py` は背景アートだけを画像モデルに作らせ、正確な文字と数値はエンジンで重ねます。画像生成は課金のため、承認フラグ（approved）が無いと呼ばない設計です
- **LLM は CLI 優先**: `llm.py` は Codex / Claude の CLI を使い、API キーなしで動きます。従量課金の Anthropic API は `STUDIO_ALLOW_PAID_ANTHROPIC_API` で明示許可した場合だけ使われ、CLI 実行時は API キー環境変数を外して認証の混在を避けます。個人のローカル利用を想定した作りで、他者へサービスとして提供する場合は各社の規約に沿って API キー方式を使ってください
- **動画化**: `api/video.py`（VOICEVOX + ffmpeg のローカル完結）と `api/video_el.py`（ElevenLabs。外部の動画バックエンドを呼ぶ薄いラッパ）

## ディレクトリ構成（主要ファイル）

```
slide-studio/
├ api/        main.py(FastAPI) / organize.py / recommend.py / llm.py / render_ai.py
│             factcheck.py copy_gate.py gap_check.py structure_gate.py design_gate.py ocr_gate.py
│             ingest.py / audience.py / store.py / video.py / video_el.py
├ engine/     build.py(PNG+PPTX 描画) / fonts.py / icons.py / themes.py
├ themes/     画風 YAML        presets/voice/  ナレーション口調プリセット
├ assets/     icons/(Lucide) / fonts/(取得スクリプトとライセンスのみ)
├ web/        index.html
└ requirements.txt
```

## 動かし方

必要なもの: Python 3、`requirements.txt` の依存、Playwright の Chromium（`python -m playwright install chromium`）、Claude CLI または Codex CLI。

```bash
pip install -r requirements.txt
python -m playwright install chromium
python assets/fonts/_fetch_fonts.py      # 和文フォント(Noto Sans JP / Noto Serif JP)を取得
python -m uvicorn api.main:app --port 8000   # slide-studio ディレクトリで実行
```

主な環境変数（`.env` を `slide-studio/` かその親ディレクトリに置けます）:

| 変数 | 用途 |
|---|---|
| `STUDIO_LLM_BACKEND` | `claude` または `codex`（未指定なら利用可能な CLI を自動選択） |
| `OPENAI_API_KEY` | AI 画像（`render_ai.py`）。使うときだけ |
| `ELEVENLABS_API_KEY` | `video_el.py`（動画化） |
| `VOICEVOX_SPEAKER_ID` | `video.py`（VOICEVOX 版の動画化）で使う話者 ID。既定値は無く、未設定でリクエストにも `speaker` が無ければエラーになります |
| `VOICEVOX_URL` | VOICEVOX エンジンの URL（任意。未指定ならローカルの標準ポート） |
| `VIDEO_BACKEND_DIR` | 外部の動画バックエンドの場所（`video_el.py`） |
| `STUDIO_ALLOW_PAID_ANTHROPIC_API` | 従量課金の API 利用を明示許可（既定は無効） |

## 制限事項（閉包）

`ast` と import 文の調査で、リポジトリ内・標準ライブラリ・`requirements.txt` のいずれにも無い import は次の 1 件です。

- **同梱していない依存（1 件）**: `anthropic`（`api/llm.py` の従量課金 API 経路でのみ遅延 import。CLI 経路では不要）
- **合成スタブ**: `api/openai_image_config.py`（画像生成の既定モデル名と quality の定数だけを持つスタブ。自分の契約に合わせて値を変更してください）
- **非同梱の外部バックエンド**: `api/video_el.py`（ElevenLabs 版の動画化）は、外部の動画バックエンドの実行体をサブプロセスで呼びます。バックエンドが無い環境ではこの機能だけが動きません。VOICEVOX 版（`video.py`）は影響を受けません
- **他の公開リポジトリへの依存**: なし（`studio-agents` が本リポジトリを呼び出す側です）
- **フォント**: `assets/fonts/` には取得スクリプトとライセンスのみを置き、フォント本体は含みません
- 外部サービス: OpenAI（画像）、ElevenLabs / VOICEVOX（音声）。取り込む資料・URL・音声合成は、それぞれの利用規約と権利者の許諾の範囲で使ってください。取り込む資料は自分が権利を持つもの、または利用許諾のあるものに限り、第三者の記事は要約と出典明記の範囲で扱ってください
- **VOICEVOX の音声を使った動画を公開する場合**は、使った話者の利用規約に沿って「VOICEVOX:<話者名>」などのクレジット表記を、動画の説明欄などに入れてください（自動では付きません）

## ライセンス

All rights reserved. 閲覧目的で公開しています。詳細は [LICENSE](LICENSE) を参照。同梱のアイコンは Lucide（ISC License, `assets/icons/LICENSE_lucide.txt`）、フォントの取得対象は SIL Open Font License 1.1（`assets/fonts/OFL_LICENSE.txt`）です。
