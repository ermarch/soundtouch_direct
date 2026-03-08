"""HTTP streaming proxy for SoundTouch TTS/audio playback.

The SoundTouch 300 cannot play one-shot MP3 URLs — it expects a persistent
HTTP audio stream. This proxy pre-fetches the source audio into memory, then
serves it repeatedly as a chunked stream so the device can buffer it reliably.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web
from aiohttp.web_exceptions import HTTPNotFound

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

STREAM_PATH = "/api/soundtouch_direct/stream/{token}"
CHUNK_SIZE = 8192


class SoundTouchStreamProxy:
    """Manages pending stream tokens → pre-fetched audio bytes."""

    def __init__(self) -> None:
        self._streams: dict[str, bytes] = {}

    async def register(self, token: str, source_url: str) -> bool:
        """Pre-fetch audio from source_url and store under token.
        
        Returns True if successful, False if the fetch failed.
        """
        _LOGGER.warning(
            "SoundTouch stream proxy: pre-fetching %s", source_url
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(source_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        _LOGGER.error(
                            "SoundTouch stream proxy: source returned HTTP %s for %s",
                            resp.status, source_url,
                        )
                        return False
                    audio_bytes = await resp.read()
                    if not audio_bytes:
                        _LOGGER.error(
                            "SoundTouch stream proxy: empty response from %s", source_url
                        )
                        return False
                    self._streams[token] = audio_bytes
                    _LOGGER.warning(
                        "SoundTouch stream proxy: pre-fetched %d bytes for token %s",
                        len(audio_bytes), token,
                    )
                    return True
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error(
                "SoundTouch stream proxy: failed to pre-fetch %s: %r", source_url, err
            )
            return False

    def get(self, token: str) -> bytes | None:
        return self._streams.get(token)

    def unregister(self, token: str) -> None:
        self._streams.pop(token, None)


class SoundTouchStreamView(HomeAssistantView):
    """Serves pre-fetched audio bytes as a persistent stream."""

    url = STREAM_PATH
    name = "api:soundtouch_direct:stream"
    requires_auth = False  # SoundTouch cannot send auth headers

    def __init__(self, proxy: SoundTouchStreamProxy) -> None:
        self._proxy = proxy

    async def get(self, request: web.Request, token: str) -> web.StreamResponse:
        """Stream pre-fetched audio to the SoundTouch device."""
        audio_bytes = self._proxy.get(token)
        if not audio_bytes:
            _LOGGER.warning("SoundTouch stream: unknown or expired token %s", token)
            raise HTTPNotFound()

        _LOGGER.warning(
            "SoundTouch stream REQUEST RECEIVED: token=%s, serving %d bytes",
            token, len(audio_bytes),
        )

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "audio/mpeg",
                "Content-Length": str(len(audio_bytes)),
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "icy-name": "TTS",
                "icy-genre": "TTS",
                "icy-metaint": "0",
            },
        )
        await response.prepare(request)

        try:
            # Send audio in chunks
            for i in range(0, len(audio_bytes), CHUNK_SIZE):
                await response.write(audio_bytes[i:i + CHUNK_SIZE])
                await asyncio.sleep(0)  # yield to event loop between chunks

            _LOGGER.warning("SoundTouch stream COMPLETED for token %s", token)

        except asyncio.CancelledError:
            _LOGGER.warning("SoundTouch stream CANCELLED for token %s", token)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("SoundTouch stream ERROR for token %s: %r", token, err)
        finally:
            self._proxy.unregister(token)

        return response


def async_setup_stream_proxy(hass: HomeAssistant) -> SoundTouchStreamProxy:
    """Register the stream proxy view and return the proxy manager."""
    proxy = SoundTouchStreamProxy()
    hass.http.register_view(SoundTouchStreamView(proxy))
    return proxy
