"""Bot-side outbound editor session. The host supplies authenticated business RPC."""
import asyncio
import json
import re
from hmac import compare_digest, digest
from secrets import token_urlsafe
from time import monotonic
from urllib.parse import urlencode

import aiohttp
from Crypto.PublicKey import ECC

from . import BASE
from .protocol import Cipher, MAX_PACKET, public_key, relay_origin


class EditorSession:
    def __init__(self, url, token, *, handler, on_close):
        if not isinstance(token, str) or len(token) < 32 or not token.isascii():
            raise ValueError("Relay service token must contain at least 32 ASCII characters")
        self.url, self.token = relay_origin(url), token
        self.handler, self.on_close = handler, on_close
        self.private = ECC.generate(curve="P-256")
        self.cipher = None
        self.secret = token_urlsafe(32)
        self.deadline = 0
        self.connected = asyncio.Event()
        self.send_lock = asyncio.Lock()
        self.pending = set()
        self.http = self.ws = self.task = None
        self.closed = False

    async def start(self):
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), trust_env=True)
        try:
            async with self.http.post(self.url + BASE + "/api/channels", headers={"x-relay-token": self.token}) as response:
                if response.status != 201:
                    raise ValueError(f"Relay refused editor session ({response.status})")
                data = await response.json()
            if (not isinstance(data, dict) or set(data) != {"id", "bot_key", "browser_key", "expires_in"}
                    or type(data["expires_in"]) is not int or not 1 <= data["expires_in"] <= 3600
                    or any(not isinstance(data[k], str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,96}", data[k])
                           for k in ("bot_key", "browser_key"))):
                raise ValueError("Invalid relay session response")
            self.id = data["id"]
            if not isinstance(self.id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", self.id):
                raise ValueError("Invalid relay channel")
            self.deadline = monotonic() + min(max(int(data["expires_in"]), 1), 3600)
            self.ws = await self.http.ws_connect(self.url.replace("https:", "wss:").replace("http:", "ws:") + BASE + f"/api/channels/{self.id}/bot", heartbeat=15, max_msg_size=MAX_PACKET)
            await self.ws.send_json({"key": data["bot_key"]})
            self.task = asyncio.create_task(self.listen())
            await asyncio.wait_for(self.connected.wait(), 10)
            if self.closed:
                raise ValueError("Relay connection closed")
            fragment = urlencode({"channel": self.id, "key": data["browser_key"], "public": public_key(self.private), "secret": self.secret})
            return self.url + BASE + "/#" + fragment
        except BaseException:
            await self.close()
            raise

    async def send(self, data):
        async with self.send_lock:
            await self.ws.send_json(self.cipher.seal(data))

    async def reply(self, request):
        try:
            result = await self.handler(request)
            await self.send({"type": "response", "id": request.get("id"), **result})
        except (ValueError, KeyError, TypeError):
            await self.send({"type": "response", "id": request.get("id"), "status": 422, "data": {"detail": "Invalid editor request"}})

    async def listen(self):
        try:
            async with asyncio.timeout(max(1, self.deadline - monotonic())):
                async for message in self.ws:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        break
                    data = json.loads(message.data)
                    if data == {"control": "connected"}:
                        self.connected.set()
                        continue
                    if not self.cipher:
                        if (not isinstance(data, dict) or set(data) != {"type", "public", "proof"} or data["type"] != "hello"
                                or not isinstance(data["public"], str) or not isinstance(data["proof"], str) or not data["proof"].isascii()):
                            raise ValueError("Invalid editor handshake")
                        expected = digest(self.secret.encode("ascii"), f"{self.id}:{data['public']}".encode(), "sha256").hex()
                        if not compare_digest(expected, data["proof"]):
                            raise ValueError("Invalid editor link")
                        self.cipher = Cipher(self.private, data["public"], self.id, server=True)
                        self.secret = None
                        await self.send({"type": "ready"})
                        continue
                    request = self.cipher.open(data)
                    if not isinstance(request, dict) or len(self.pending) >= 8:
                        raise ValueError("Invalid editor or too many requests")
                    if request.get("type") != "request" or not isinstance(request.get("id"), str) or len(request["id"]) > 64:
                        raise ValueError("Invalid editor request")
                    task = asyncio.create_task(self.reply(request))
                    self.pending.add(task)
                    task.add_done_callback(self.pending.discard)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError):
            pass
        finally:
            self.connected.set()
            await self.close()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        await self.on_close()
        for task in list(self.pending):
            task.cancel()
        await asyncio.gather(*list(self.pending), return_exceptions=True)
        if self.ws:
            await self.ws.close()
        if self.http:
            await self.http.close()
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.private = self.cipher = self.secret = None
