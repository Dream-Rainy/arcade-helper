"""Independent relay transport, trust boundary, and authenticated packet regressions."""
import asyncio
import json
import socket
from contextlib import asynccontextmanager
from hmac import digest
from secrets import token_urlsafe
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("Crypto")
pytest.importorskip("fastapi")
import aiohttp
import uvicorn
from Crypto.PublicKey import ECC
from arcade_helper.web import BASE
from arcade_helper.web.client import EditorSession
from arcade_helper.web.protocol import Cipher, public_key
from arcade_helper.web.relay import create_app


@asynccontextmanager
async def relay_server():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    token = token_urlsafe(32)
    app = create_app(token=token, public_url=origin)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    for _ in range(100):
        if server.started:
            break
        if task.done():
            await task
        await asyncio.sleep(.01)
    assert server.started
    try:
        yield origin, token, app
    finally:
        server.should_exit = True
        await task
        listener.close()


def test_cipher_replay_tamper_direction_and_channel_are_rejected():
    server, browser = ECC.generate(curve="P-256"), ECC.generate(curve="P-256")
    outbound = Cipher(browser, public_key(server), "channel-a", server=False)
    inbound = Cipher(server, public_key(browser), "channel-a", server=True)
    frame = outbound.seal({"operation": "ADD_ALIAS", "private_reason": "private"})
    assert "private" not in json.dumps(frame)
    assert inbound.open(frame)["private_reason"] == "private"
    with pytest.raises(ValueError): inbound.open(frame)
    fresh = outbound.seal({"operation": "REVIEW"})
    tampered = dict(fresh, data=fresh["data"][:-2]+"AA")
    with pytest.raises(ValueError): inbound.open(tampered)
    assert inbound.received == 1
    assert inbound.open(fresh)["operation"] == "REVIEW"
    other = Cipher(server, public_key(browser), "channel-b", server=True)
    with pytest.raises(ValueError): other.open(frame)
    with pytest.raises(ValueError): outbound.open(frame)


async def test_relay_credentials_single_peer_forwarding_and_disconnect_cleanup():
    async with relay_server() as (origin, token, app), aiohttp.ClientSession() as http:
        bad = await http.post(origin+BASE+"/api/channels")
        assert bad.status == 403
        response = await http.post(origin+BASE+"/api/channels", headers={"x-relay-token": token})
        data = await response.json()
        assert response.status == 201
        path = origin.replace("http:", "ws:")+BASE+f"/api/channels/{data['id']}"
        with pytest.raises(aiohttp.WSServerHandshakeError):
            await http.ws_connect(path+"/browser", origin="https://wrong.example")
        bot = await http.ws_connect(path+"/bot")
        await bot.send_json({"key": data["bot_key"]})
        assert (await bot.receive_json())["control"] == "connected"
        browser = await http.ws_connect(path+"/browser", origin=origin)
        await browser.send_json({"key": data["browser_key"]})
        assert (await browser.receive_json())["control"] == "connected"
        duplicate = await http.ws_connect(path+"/browser", origin=origin)
        await duplicate.send_json({"key": data["browser_key"]})
        assert (await duplicate.receive()).type == aiohttp.WSMsgType.CLOSE
        assert data["id"] in app.state.channels
        await browser.send_str('{"seq":1,"data":"opaque ciphertext"}')
        assert await bot.receive_str() == '{"seq":1,"data":"opaque ciphertext"}'
        await browser.close()
        await bot.receive()
        assert data["id"] not in app.state.channels
        await bot.close()


async def test_outbound_editor_link_authentication_and_tampered_requests():
    async with relay_server() as (origin, token, app), aiohttp.ClientSession() as http:
        calls, closed = [], asyncio.Event()
        async def dispatch(request):
            calls.append(request)
            return {"status": 200, "data": {"applied": request["path"]}}
        async def on_close(): closed.set()
        session = EditorSession(origin, token, handler=dispatch, on_close=on_close)
        url = await session.start()
        assert session.token not in url
        fragment = parse_qs(urlsplit(url).fragment)
        secret = fragment["secret"][0]
        assert secret not in urlsplit(url).path and secret not in urlsplit(url).query
        data = app.state.channels[session.id]
        browser = await http.ws_connect(origin.replace("http:", "ws:")+BASE+f"/api/channels/{session.id}/browser", origin=origin)
        await browser.send_json({"key": data.tokens["browser"]})
        await browser.receive_json()
        private = ECC.generate(curve="P-256")
        cipher = Cipher(private, public_key(session.private), session.id, server=False)
        public = public_key(private)
        proof = digest(secret.encode(), f"{session.id}:{public}".encode(), "sha256").hex()
        await browser.send_json({"type": "hello", "public": public, "proof": proof})
        assert cipher.open(await browser.receive_json()) == {"type": "ready"}
        assert not calls
        await browser.send_json(cipher.seal({"type": "request", "id": "one", "path": "me"}))
        reply = cipher.open(await browser.receive_json())
        assert reply["data"] == {"applied": "me"} and len(calls) == 1
        broken = cipher.seal({"type": "request", "id": "two", "path": "requests"})
        broken["data"] = broken["data"][:-2]+"AA"
        await browser.send_json(broken)
        await asyncio.wait_for(closed.wait(), 2)
        assert len(calls) == 1
        await browser.close()

        # Relay channel credentials alone, or a proof copied to another public key,
        # must never grant the account behind the link.
        for invalid in ("missing_proof", "wrong_secret", "changed_public"):
            closed.clear()
            session = EditorSession(origin, token, handler=dispatch, on_close=on_close)
            url = await session.start()
            secret = parse_qs(urlsplit(url).fragment)["secret"][0]
            hello = {"type": "hello", "public": public,
                     "proof": digest(secret.encode(), f"{session.id}:{public}".encode(), "sha256").hex()}
            if invalid == "missing_proof":
                hello.pop("proof")
            elif invalid == "wrong_secret":
                hello["proof"] = digest(token_urlsafe(32).encode(), f"{session.id}:{public}".encode(), "sha256").hex()
            else:
                hello["public"] = public_key(ECC.generate(curve="P-256"))
            data = app.state.channels[session.id]
            browser = await http.ws_connect(origin.replace("http:", "ws:")+BASE+f"/api/channels/{session.id}/browser", origin=origin)
            await browser.send_json({"key": data.tokens["browser"]})
            await browser.receive_json()
            await browser.send_json(hello)
            await asyncio.wait_for(closed.wait(), 2)
            assert len(calls) == 1
            await browser.close()
