"""Volcano Agent Plan voice adapters (seed-tts-2.0 / doubao-seed-asr-2.0).

TTS talks to the Agent-Plan-exclusive speech endpoint:

    POST https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional

STT streams audio to the Agent-Plan-exclusive ASR endpoint:

    WSS  wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async

Credentials use the SAME Agent Plan key as the Ark LLM/embedding channels
(``X-Api-Key`` header) — the console-issued speech key is NOT required.
The ``/plan/`` URL segment is mandatory: the sibling public endpoints
(``/api/v3/tts``, ``/api/v3/sauc``) bill per-use even for plan subscribers,
so these adapters refuse to send when the configured URL lacks the
``/plan/`` segment.

Speaker notes: ``seed-tts-2.0`` requires a speaker from its own voice
catalog; omitting it (or passing a voice from another resource) fails with
``resource ID is mismatched with speaker related resource``. Configure the
speaker via the model's ``voice`` field.

STT notes (empirically verified against the plan endpoint, 2026-09-19):
the wire protocol is the Volcano binary framing (4-byte header, int32
sequence, int32 gzip size, gzip payload). The first client frame carries
the session config (JSON key is ``audio``, NOT ``audio_config``); audio
frame sequences start at 2; the final audio frame flips the flag to
``NEG_WITH_SEQUENCE`` and negates its sequence. The async endpoint only
pushes a frame when the recognised text changes, so the client keeps
reading until a silence timeout and returns the last text seen.
"""

from __future__ import annotations

import struct
import uuid
from typing import Any

import httpx

from deeptutor.services.voice.base import (
    BaseSTTAdapter,
    BaseTTSAdapter,
    VoiceProviderError,
)

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


class VolcPlanSTTAdapter(BaseSTTAdapter):
    """Stream audio over the plan-exclusive ``/plan/sauc`` ASR endpoint.

    Implements the doubao-seed-asr-2.0 binary WebSocket protocol using the
    SAME Agent Plan key as every other plan channel (``X-Api-Key``).
    """

    _CHUNK = 32000  # bytes per audio frame (~2s of 128kbps audio)

    def _endpoint(self, config) -> str:
        if not config.base_url:
            raise VoiceProviderError("No endpoint URL configured for STT.")
        if PLAN_URL_MARKER not in config.base_url:
            # Guardrail: the public /api/v3/sauc line bills per-use even for
            # plan subscribers. Never silently fall back to it.
            raise VoiceProviderError(
                "Volcano STT must use the Agent-Plan exclusive URL containing "
                f"'{PLAN_URL_MARKER}' (got: {config.base_url}). The public "
                "endpoint would incur extra charges."
            )
        return config.base_url

    @staticmethod
    def _audio_format(filename: str, content_type: str) -> str:
        name = (filename or "").lower()
        for ext in ("wav", "mp3", "m4a", "flac", "ogg", "webm"):
            if name.endswith("." + ext):
                return ext
        if "mpeg" in (content_type or ""):
            return "mp3"
        return "mp3"

    async def transcribe(
        self,
        audio: bytes,
        config,
        *,
        filename: str = "audio.webm",
        content_type: str = "application/octet-stream",
    ) -> str:
        import asyncio
        import gzip
        import json as _json

        import websockets

        url = self._endpoint(config)
        if not config.api_key:
            raise VoiceProviderError("No API key configured for STT.")
        headers = {
            "X-Api-Key": config.api_key,
            "X-Api-Resource-Id": config.model or "volc.seedasr.sauc.duration",
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        fmt = self._audio_format(filename, content_type)

        proto_v1 = 0b0001
        full_request = 0b0001
        audio_only = 0b0010
        server_error = 0b1111
        pos_seq = 0b0001
        neg_with_seq = 0b0011
        json_ser = 0b0001
        gzip_comp = 0b0001

        state = {"seq": 0}
        texts: list[str] = []
        errors: list[str] = []

        def parse_frame(raw) -> None:
            if isinstance(raw, str):
                return
            message_type = (raw[1] >> 4) & 0b1111
            size = struct.unpack(">I", raw[8:12])[0]
            payload = raw[12:12 + size]
            try:
                payload = gzip.decompress(payload).decode("utf-8", "replace")
            except Exception:
                payload = payload.decode("utf-8", "replace")
            if message_type == server_error:
                errors.append(payload)
                return
            try:
                result = (_json.loads(payload) or {}).get("result") or {}
            except Exception:
                return
            text = (result.get("text") or "").strip()
            if text:
                texts.append(text)

        def config_frame() -> bytes:
            payload = _json.dumps({
                "user": {"uid": "yuedu"},
                "audio": {
                    "format": fmt,
                    "codec": "raw",
                    "rate": 16000,
                    "bits": 16,
                    "channel": 1,
                },
                "request": {
                    "model_name": "bigmodel",
                    "enable_punc": True,
                    "enable_itn": True,
                    "show_utterances": True,
                },
            }).encode()
            state["seq"] += 1
            compressed = gzip.compress(payload)
            return (
                bytes([
                    (proto_v1 << 4) | 1,
                    (full_request << 4) | pos_seq,
                    (json_ser << 4) | gzip_comp,
                    0x00,
                ])
                + struct.pack(">i", state["seq"])
                + struct.pack(">I", len(compressed))
                + compressed
            )

        def audio_frame(chunk: bytes, *, last: bool) -> bytes:
            state["seq"] += 1
            wire_seq = -state["seq"] if last else state["seq"]
            compressed = gzip.compress(chunk)
            return (
                bytes([
                    (proto_v1 << 4) | 1,
                    (audio_only << 4) | (neg_with_seq if last else pos_seq),
                    (json_ser << 4) | gzip_comp,
                    0x00,
                ])
                + struct.pack(">i", wire_seq)
                + struct.pack(">I", len(compressed))
                + compressed
            )

        async def reader(wsc) -> None:
            while True:
                try:
                    raw = await wsc.recv()
                except Exception:
                    return
                parse_frame(raw)
                if errors:
                    return

        async def run() -> str:
            loop = asyncio.get_running_loop()
            async with websockets.connect(
                url,
                additional_headers=headers,
                max_size=16 * 1024 * 1024,
                open_timeout=20,
            ) as wsc:
                reader_task = asyncio.create_task(reader(wsc))
                try:
                    await wsc.send(config_frame())
                    for offset in range(0, len(audio), self._CHUNK):
                        chunk = audio[offset:offset + self._CHUNK]
                        last = offset + self._CHUNK >= len(audio)
                        await wsc.send(audio_frame(chunk, last=last))
                        # Gentle pacing keeps the server's 8s inter-packet
                        # watchdog happy without material slowdown.
                        await asyncio.sleep(0.03)
                    # Drain results until the server goes quiet for a while.
                    deadline = loop.time() + 30
                    while loop.time() < deadline:
                        if errors:
                            break
                        try:
                            raw = await asyncio.wait_for(wsc.recv(), timeout=5)
                            parse_frame(raw)
                            if texts:
                                deadline = loop.time() + 8
                        except asyncio.TimeoutError:
                            break
                        except Exception:
                            break
                finally:
                    reader_task.cancel()
                    try:
                        await wsc.close()
                    except Exception:
                        pass
            if errors:
                raise VoiceProviderError(f"Volcano ASR error: {errors[-1][:300]}")
            return (texts[-1] if texts else "").strip()

        try:
            timeout = getattr(config, "request_timeout", None) or 600
            return await asyncio.wait_for(run(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise VoiceProviderError("Volcano ASR timed out.") from exc


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
