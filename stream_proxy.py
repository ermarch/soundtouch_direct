"""HTTP streaming proxy for SoundTouch TTS/audio playback.

The SoundTouch 300 (and similar models) cannot play one-shot MP3 URLs directly.
It only accepts persistent HTTP audio streams (like internet radio). This module
registers a HA HTTP view that fetches a source audio URL and re-serves it as a
chunked, persistent stream that the SoundTouch can tune into like a radio station.
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

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)

STREAM_PATH = "/api/soundtouch_direct/stream/{token}"
CHUNK_SIZE = 4096


class SoundTouchStreamProxy:
    """Manages pending stream tokens → source URLs."""

    def __init__(self) -> None:
        self._streams: dict[str, str] = {}

    def register(self, token: str, source_url: str) -> None:
        """Register a token mapped to a source URL."""
        self._streams[token] = source_url
        _LOGGER.debug("Registered stream token %s -> %s", token, source_url)

    def get(self, token: str) -> str | None:
        """Look up a source URL by token (does NOT consume it — stream may reconnect)."""
        return self._streams.get(token)

    def unregister(self, token: str) -> None:
        """Remove a token."""
        self._streams.pop(token, None)


class SoundTouchStreamView(HomeAssistantView):
    """Serves a source audio URL as a persistent chunked stream."""

    url = STREAM_PATH
    name = "api:soundtouch_direct:stream"
    requires_auth = False  # SoundTouch has no way to send auth headers

    def __init__(self, proxy: SoundTouchStreamProxy) -> None:
        self._proxy = proxy

    async def get(self, request: web.Request, token: str) -> web.StreamResponse:
        """Stream audio from the registered source URL."""
        source_url = self._proxy.get(token)
        if not source_url:
            _LOGGER.warning("SoundTouch stream: unknown token %s", token)
            raise HTTPNotFound()

        _LOGGER.warning("SoundTouch stream REQUEST RECEIVED: token=%s source=%s", token, source_url)

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "audio/mpeg",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Transfer-Encoding": "chunked",
                "icy-name": "TTS",
                "icy-genre": "TTS",
                "icy-metaint": "0",
            },
        )
        await response.prepare(request)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(source_url) as resp:
                    if resp.status != 200:
                        _LOGGER.error(
                            "SoundTouch stream: source URL returned %s for %s",
                            resp.status,
                            source_url,
                        )
                        return response

                    chunk_count = 0
                    async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                        await response.write(chunk)
                        chunk_count += 1
                    _LOGGER.warning("SoundTouch stream COMPLETED: %d chunks sent for token %s", chunk_count, token)

        except asyncio.CancelledError:
            _LOGGER.warning("SoundTouch stream CANCELLED for token %s", token)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("SoundTouch stream error for token %s: %r", token, err)
        finally:
            # Clean up token after stream ends
            self._proxy.unregister(token)
            _LOGGER.debug("SoundTouch stream ended for token %s", token)

        return response


def async_setup_stream_proxy(hass: HomeAssistant) -> SoundTouchStreamProxy:
    """Register the stream proxy view and return the proxy manager."""
    proxy = SoundTouchStreamProxy()
    hass.http.register_view(SoundTouchStreamView(proxy))
    return proxy
