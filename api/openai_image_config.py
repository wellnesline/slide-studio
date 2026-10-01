"""synthetic stub: 公開用の最小スタブ。画像生成の既定モデル名と品質の定数だけを持つ。

非公開の共通設定モジュールの代わりに、ここで固定値を宣言する。実運用では自分の契約・利用可能なモデルに合わせて値を変更すること。
"""
from __future__ import annotations

DEFAULT_IMAGE_MODEL = "gpt-image-2.5-sunburst"
DEFAULT_IMAGE_QUALITY = "low"

_ALLOWED_QUALITIES = {"low", "medium", "high", "auto"}


def validate_image_defaults(model: str, quality: str) -> None:
    if not model or not isinstance(model, str):
        raise ValueError("image model must be a non-empty string")
    if quality not in _ALLOWED_QUALITIES:
        raise ValueError(f"unsupported image quality: {quality!r}")
