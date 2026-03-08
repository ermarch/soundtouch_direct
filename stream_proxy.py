"""HTTP streaming proxy for SoundTouch TTS/audio playback.

The SoundTouch 300 behaves like a radio client — it expects the HTTP connection
to stay open indefinitely. Closing it immediately after sending audio causes the
device to stall (yellow LED, no sound). We therefore:
  1. Pre-fetch the audio into memory before sending the /select command
  2. Send the audio bytes to the device
  3. Hold the connection open with silence padding until the device disconnects
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import web
from aiohttp.web_exceptions import HTTPNotFound

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

STREAM_PATH = "/api/soundtouch_direct/stream/{token}"
CHUNK_SIZE = 8192

# Silent MP3 frame (1 frame of 128kbps silence) used to pad the stream open
# so the SoundTouch doesn't close the connection before it's done playing.
# This is a valid MP3 frame header + silence data.
_SILENT_MP3_FRAME = bytes([
    0xFF, 0xFB, 0x90, 0x00,  # MP3 frame header: MPEG1, Layer3, 128kbps, 44100Hz, stereo
]) + bytes(413)  # 417 bytes total per frame at 128kbps


class SoundTouchStreamProxy:
    """Manages stream tokens mapped to pre-fetched audio bytes."""

    def __init__(self) -> None:
        self._streams: dict[str, bytes] = {}

    async def register(self, token: str, source_url: str) -> bool:
        """Pre-fetch audio from source_url and store under token."""
        _LOGGER.warning("SoundTouch proxy: pre-fetching %s", source_url)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    source_url,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.error(
                            "SoundTouch proxy: source returned HTTP %s for %s",
                            resp.status, source_url,
                        )
                        return False
                    audio_bytes = await resp.read()
                    if not audio_bytes:
                        _LOGGER.error("SoundTouch proxy: empty response from %s", source_url)
                        return False
                    self._streams[token] = audio_bytes
                    _LOGGER.warning(
                        "SoundTouch proxy: stored %d bytes for token %s",
                        len(audio_bytes), token,
                    )
                    return True
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("SoundTouch proxy: pre-fetch failed for %s: %r", source_url, err)
            return False

    def get(self, token: str) -> bytes | None:
        return self._streams.get(token)

    def unregister(self, token: str) -> None:
        self._streams.pop(token, None)


class SoundTouchStreamView(HomeAssistantView):
    """Serves pre-fetched audio then holds the connection open with silence."""

    url = STREAM_PATH
    name = "api:soundtouch_direct:stream"
    requires_auth = False  # SoundTouch cannot send auth headers

    def __init__(self, proxy: SoundTouchStreamProxy) -> None:
        self._proxy = proxy

    async def get(self, request: web.Request, token: str) -> web.StreamResponse:
        """Stream audio to the SoundTouch, then pad with silence to keep connection alive."""
        audio_bytes = self._proxy.get(token)
        if not audio_bytes:
            _LOGGER.warning("SoundTouch stream: unknown token %s", token)
            raise HTTPNotFound()

        _LOGGER.warning(
            "SoundTouch stream: connection received for token %s (%d bytes)",
            token, len(audio_bytes),
        )

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "audio/mpeg",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "icy-name": "TTS",
                "icy-genre": "Speech",
                "icy-metaint": "0",
            },
        )
        await response.prepare(request)

        try:
            # Send the actual audio
            for i in range(0, len(audio_bytes), CHUNK_SIZE):
                await response.write(audio_bytes[i:i + CHUNK_SIZE])
                await asyncio.sleep(0)

            _LOGGER.warning("SoundTouch stream: audio sent for token %s, holding open", token)

            # Keep the connection alive with silent MP3 frames.
            # The SoundTouch needs time to decode and play the buffered audio.
            # We send ~10 minutes worth of silence then give up.
            # The device will disconnect naturally when it's done playing.
            max_silence_seconds = 600
            silence_interval = 0.5  # send a silent frame every 500ms
            elapsed = 0.0
            while elapsed < max_silence_seconds:
                await asyncio.sleep(silence_interval)
                await response.write(_SILENT_MP3_FRAME)
                elapsed += silence_interval

        except asyncio.CancelledError:
            _LOGGER.warning("SoundTouch stream: cancelled for token %s", token)
        except ConnectionResetError:
            _LOGGER.warning("SoundTouch stream: device disconnected for token %s", token)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("SoundTouch stream: error for token %s: %r", token, err)
        finally:
            self._proxy.unregister(token)
            _LOGGER.warning("SoundTouch stream: closed for token %s", token)

        return response


def async_setup_stream_proxy(hass: HomeAssistant) -> SoundTouchStreamProxy:
    """Register the stream proxy view and return the proxy manager."""
    proxy = SoundTouchStreamProxy()
    hass.http.register_view(SoundTouchStreamView(proxy))
    return proxy
