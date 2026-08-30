"""Volcano Agent Plan TTS adapter (seed-tts-2.0).

Talks to the Agent-Plan-exclusive speech endpoint:

    POST https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional

Credentials use the SAME Agent Plan key as the Ark LLM/embedding channels
(``X-Api-Key`` header) — the console-issued speech key is NOT required.
The ``/plan/`` URL segment is mandatory: the sibling public endpoint
(``/api/v3/tts``) bills per-use even for plan subscribers, so this adapter
refuses to send when the configured URL lacks the ``/plan/`` segment.

Speaker notes: ``seed-tts-2.0`` requires a speaker from its own voice
catalog; omitting it (or passing a voice from another resource) fails with
``resource ID is mismatched with speaker related resource``. Configure the
speaker via the model's ``voice`` field.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from deeptutor.services.voice.base import BaseTTSAdapter, VoiceProviderError

PLAN_URL_MARKER = "/plan/"


class VolcPlanTTSAdapter(BaseTTSAdapter):
    """POST the full plan-exclusive TTS URL, returning raw audio bytes."""

    async def synthesize(self, text: str, config) -> tuple[bytes, str]:
        if not config.base_url:
            raise VoiceProviderError("No endpoint URL configured for TTS.")
        if PLAN_URL_MARKER not in config.base_url:
            # Guardrail: the public /api/v3/tts line bills per-use even for
            # plan subscribers. Never silently fall back to it.
            raise VoiceProviderError(
                "Volcano TTS must use the Agent-Plan exclusive URL containing "
                f"'{PLAN_URL_MARKER}' (got: {config.base_url}). The public "
                "endpoint would incur extra charges."
            )
        if not config.voice:
            raise VoiceProviderError(
                "seed-tts-2.0 requires a speaker from its voice catalog; "
                "set the model's voice (empty speaker fails with resource "
                "mismatch)."
            )

        url = config.base_url
        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": config.api_key,
            "X-Api-Resource-Id": config.model,
            "X-Api-Request-Id": str(uuid.uuid4()),
            **(config.extra_headers or {}),
        }
        audio_params: dict[str, Any] = {
            "format": (config.response_format or "mp3").lower(),
            "sample_rate": 24000,
        }
        payload: dict[str, Any] = {
            "user": {"uid": "yuedu"},
            "req_params": {
                "text": text,
                "speaker": config.voice,
                "audio_params": audio_params,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=config.request_timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise VoiceProviderError(f"TTS request error: {exc}") from exc

        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            # Plan API reports failures as JSON bodies, sometimes with HTTP 200.
            detail = _json_error_detail(resp)
            if detail:
                raise VoiceProviderError(f"Volcano TTS error: {detail}")
        audio = resp.content
        if not audio:
            raise VoiceProviderError("Volcano TTS returned empty audio.")
        fmt = (config.response_format or "mp3").lower()
        return audio, f"audio/{'mpeg' if fmt == 'mp3' else fmt}"

    async def transcribe(self, audio: bytes, config, **kwargs):
        raise VoiceProviderError(
            "Volcano plan ASR uses the wss endpoint "
            "(wss://openspeech.bytedance.com/api/v3/plan/asr) and is not "
            "implemented yet; use another STT provider."
        )


def _json_error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except Exception:
        return ""
    if isinstance(body, dict):
        header = body.get("header") or body
        code = header.get("code", "")
        message = header.get("message", "")
        return f"{code} {message}".strip()
    return ""
