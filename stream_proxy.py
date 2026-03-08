"""HTTP streaming proxy for SoundTouch TTS/audio playback.

The SoundTouch firmware requires the LOCAL_INTERNET_RADIO source to play
arbitrary HTTP audio streams. This source expects:
  1. A JSON descriptor file at a URL passed to /select
  2. The JSON contains the actual audio stream URL

We serve both from HA's built-in HTTP server:
  - /api/soundtouch_direct/station/{token}.json  → JSON descriptor
  - /api/soundtouch_direct/stream/{token}        → audio (persistent stream)

IMPORTANT: The SoundTouch firmware requires plain HTTP, not HTTPS.
The proxy always serves both endpoints over HTTP.

Audio streaming strategy:
  The device behaves like a radio client — it expects the HTTP connection to
  stay open. We pre-fetch the TTS audio into memory, send it, then hold the
  connection open with silent MP3 padding frames so the device does not cut
  the stream before it finishes playing.
"""
from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
from aiohttp import web
from aiohttp.web_exceptions import HTTPNotFound

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

STATION_PATH = "/api/soundtouch_direct/station/{token}.json"
STREAM_PATH = "/api/soundtouch_direct/stream/{token}"
CHUNK_SIZE = 8192

# A valid silent MP3 frame (MPEG1, Layer 3, 128kbps, 44100Hz, Joint Stereo).
# Sent after the real audio to keep the connection alive while the device plays.
_SILENT_FRAME = bytes([0xFF, 0xFB, 0x90, 0x64]) + bytes(413)


class SoundTouchStreamProxy:
    """Manages stream tokens mapped to pre-fetched audio bytes."""

    def __init__(self) -> None:
        self._streams: dict[str, bytes] = {}

    async def register(self, token: str, source_url: str) -> bool:
        """Pre-fetch audio from source_url and store under token. Returns success."""
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
                    data = await resp.read()
                    if not data:
                        _LOGGER.error("SoundTouch proxy: empty response from %s", source_url)
                        return False
                    self._streams[token] = data
                    _LOGGER.warning(
                        "SoundTouch proxy: stored %d bytes for token %s",
                        len(data), token,
                    )
                    return True
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("SoundTouch proxy: pre-fetch failed for %s: %r", source_url, err)
            return False

    def get(self, token: str) -> bytes | None:
        return self._streams.get(token)

    def unregister(self, token: str) -> None:
        self._streams.pop(token, None)


class SoundTouchStationView(HomeAssistantView):
    """Serves the JSON station descriptor that LOCAL_INTERNET_RADIO expects."""

    url = STATION_PATH
    name = "api:soundtouch_direct:station"
    requires_auth = False

    def __init__(self, proxy: SoundTouchStreamProxy, ha_base_url: str) -> None:
        self._proxy = proxy
        self._ha_base_url = ha_base_url

    async def get(self, request: web.Request, token: str) -> web.Response:
        if self._proxy.get(token) is None:
            raise HTTPNotFound()

        # Force HTTP — SoundTouch firmware rejects HTTPS stream URLs
        base = self._ha_base_url
        if base.startswith("https://"):
            base = "http://" + base[8:]

        stream_url = f"{base}/api/soundtouch_direct/stream/{token}"
        descriptor = {
            "audio": {
                "hasPlaylist": False,
                "isRealtime": True,
                "streamUrl": stream_url,
            },
            "imageUrl": "",
            "name": "TTS",
            "streamType": "liveRadio",
        }
        _LOGGER.warning(
            "SoundTouch station JSON served for token %s, stream URL: %s",
            token, stream_url,
        )
        return web.Response(
            body=json.dumps(descriptor),
            content_type="application/json",
        )


class SoundTouchStreamView(HomeAssistantView):
    """Serves pre-fetched audio then holds connection open with silence padding."""

    url = STREAM_PATH
    name = "api:soundtouch_direct:stream"
    requires_auth = False

    def __init__(self, proxy: SoundTouchStreamProxy) -> None:
        self._proxy = proxy

    async def get(self, request: web.Request, token: str) -> web.StreamResponse:
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
            # Send the real audio
            for i in range(0, len(audio_bytes), CHUNK_SIZE):
                await response.write(audio_bytes[i:i + CHUNK_SIZE])
                await asyncio.sleep(0)

            _LOGGER.warning("SoundTouch stream: audio sent for token %s, holding open", token)

            # Hold connection open with silence so the device finishes playing.
            # It will disconnect naturally when done; we give up after 10 minutes.
            for _ in range(1200):  # 1200 × 0.5s = 600s = 10 minutes
                await asyncio.sleep(0.5)
                await response.write(_SILENT_FRAME)

        except (asyncio.CancelledError, ConnectionResetError):
            _LOGGER.warning("SoundTouch stream: device disconnected for token %s", token)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("SoundTouch stream: error for token %s: %r", token, err)
        finally:
            self._proxy.unregister(token)
            _LOGGER.warning("SoundTouch stream: closed for token %s", token)

        return response


def async_setup_stream_proxy(
    hass: HomeAssistant, ha_base_url: str
) -> SoundTouchStreamProxy:
    """Register the stream proxy views and return the proxy manager."""
    proxy = SoundTouchStreamProxy()
    hass.http.register_view(SoundTouchStationView(proxy, ha_base_url))
    hass.http.register_view(SoundTouchStreamView(proxy))
    return proxy
