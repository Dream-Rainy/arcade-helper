"""An independently hosted, bounded relay for two outbound editor sockets."""
import argparse
import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from hmac import compare_digest
from secrets import token_urlsafe

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

from . import ASSET_DIR, BASE
from .protocol import MAX_PACKET, relay_origin

HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}


@dataclass
class Channel:
    id: str
    tokens: dict = field(repr=False)
    peers: dict = field(default_factory=dict, repr=False)
    locks: dict = field(default_factory=lambda: {"bot": asyncio.Lock(), "browser": asyncio.Lock()}, repr=False)
    timer: asyncio.Task | None = field(default=None, repr=False)


def create_app(*, token, public_url, ttl=3600, max_channels=256):
    if not isinstance(token, str) or len(token) < 32 or not token.isascii():
        raise ValueError("ARCADE_RELAY_TOKEN must contain at least 32 ASCII characters")
    expected_origin = relay_origin(public_url)
    channels = {}

    async def close(channel):
        if channels.get(channel.id) is not channel:
            return
        channels.pop(channel.id, None)
        if channel.timer is not asyncio.current_task():
            channel.timer.cancel()
        for peer in list(channel.peers.values()):
            try:
                await peer.close(code=1000)
            except (RuntimeError, WebSocketDisconnect):
                pass

    async def expire(channel):
        await asyncio.sleep(ttl)
        await close(channel)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await asyncio.gather(*(close(c) for c in list(channels.values())))

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.channels = channels

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers.update(HEADERS)
        return response

    @app.get(BASE + "/")
    async def index():
        return HTMLResponse((ASSET_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get(BASE + "/{asset}")
    async def asset(asset: str):
        if asset not in {"portal.css", "portal.js", "relay.js", "alias-view.html"}:
            raise HTTPException(404)
        return FileResponse(ASSET_DIR / asset, media_type="text/html" if asset.endswith("html") else "text/css" if asset.endswith("css") else "application/javascript")

    @app.post(BASE + "/api/channels", status_code=201)
    async def create(request: Request):
        supplied = request.headers.get("x-relay-token", "")
        if not supplied.isascii() or not compare_digest(token, supplied):
            raise HTTPException(403, "Invalid relay service token")
        if len(channels) >= max_channels:
            raise HTTPException(503, "Relay session capacity reached")
        channel = Channel(token_urlsafe(24), {role: token_urlsafe(32) for role in ("bot", "browser")})
        channels[channel.id] = channel
        channel.timer = asyncio.create_task(expire(channel))
        return {"id": channel.id, "bot_key": channel.tokens["bot"], "browser_key": channel.tokens["browser"], "expires_in": ttl}

    @app.websocket(BASE + "/api/channels/{channel_id}/{role}")
    async def socket(websocket: WebSocket, channel_id: str, role: str):
        channel = channels.get(channel_id)
        if not channel or role not in channel.tokens or (role == "browser" and websocket.headers.get("origin") != expected_origin):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        joined = False
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            if len(raw) > 256:
                raise ValueError("Invalid channel credentials")
            auth = json.loads(raw)
            key = auth.get("key") if isinstance(auth, dict) else None
            if not isinstance(key, str) or not key.isascii() or not compare_digest(channel.tokens[role], key):
                raise ValueError("Invalid channel credentials")
            if role in channel.peers or channels.get(channel.id) is not channel:
                raise ValueError("Editor role is already connected or expired")
            channel.peers[role] = websocket
            joined = True
            await websocket.send_json({"control": "connected"})
            other = "browser" if role == "bot" else "bot"
            while True:
                raw = await websocket.receive_text()
                if len(raw.encode()) > MAX_PACKET:
                    raise ValueError("Editor message is too large")
                peer = channel.peers.get(other)
                if not peer:
                    raise ValueError("Other editor endpoint is offline")
                async with channel.locks[other]:
                    await peer.send_text(raw)
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, RuntimeError):
            pass
        finally:
            if joined:
                await close(channel)
            else:
                try:
                    await websocket.close(code=1008)
                except RuntimeError:
                    pass

    return app


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description="Independent arcade alias editor relay (no bot or database).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    app = create_app(token=os.environ.get("ARCADE_RELAY_TOKEN"), public_url=os.environ.get("ARCADE_RELAY_ORIGIN", f"http://127.0.0.1:{args.port}"))
    uvicorn.run(app, host=args.host, port=args.port, ws_max_size=MAX_PACKET, proxy_headers=True, forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))


if __name__ == "__main__":
    main()
