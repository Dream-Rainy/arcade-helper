"""Ephemeral P-256 key agreement and authenticated, ordered editor packets."""
import base64
import json
from urllib.parse import urlsplit

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.DH import key_agreement
from Crypto.Protocol.KDF import HKDF
from Crypto.PublicKey import ECC

MAX_PACKET = 1_000_000
MAX_DATA = 512_000
CONTEXT = b"arcade-alias-editor-v1"


def relay_origin(url):
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Relay URL must be an HTTP(S) origin without a path or credentials")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Public relay connections require HTTPS")
    port = parsed.port
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    suffix = f":{port}" if port and port != {"http": 80, "https": 443}[parsed.scheme] else ""
    return f"{parsed.scheme}://{host}{suffix}"


def public_key(key):
    return base64.b64encode(key.public_key().export_key(format="SEC1")).decode("ascii")


class Cipher:
    def __init__(self, private, peer, channel, *, server):
        raw = base64.b64decode(peer, validate=True)
        if len(raw) != 65 or raw[0] != 4:
            raise ValueError("Invalid P-256 public key")
        other = ECC.import_key(raw, curve_name="P-256")
        self.key = key_agreement(static_priv=private, static_pub=other,
            kdf=lambda secret: HKDF(secret, 32, channel.encode(), SHA256, context=CONTEXT))
        self.channel = channel.encode()
        self.outbound, self.inbound = (b"SRVR", b"CLNT") if server else (b"CLNT", b"SRVR")
        self.sent = self.received = 0

    def seal(self, data):
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > MAX_DATA:
            raise ValueError("Editor response is too large; narrow the query")
        self.sent += 1
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=self.outbound + self.sent.to_bytes(8, "big"))
        cipher.update(self.channel)
        encrypted, tag = cipher.encrypt_and_digest(raw)
        return {"seq": self.sent, "data": base64.b64encode(encrypted + tag).decode("ascii")}

    def open(self, packet):
        if not isinstance(packet, dict) or set(packet) != {"seq", "data"}:
            raise ValueError("Invalid editor packet")
        seq, data = packet["seq"], packet["data"]
        if type(seq) is not int or seq != self.received + 1 or seq > 2**53 - 1:
            raise ValueError("Replayed or out-of-order editor packet")
        if not isinstance(data, str) or len(data) > MAX_PACKET:
            raise ValueError("Invalid editor ciphertext")
        raw = base64.b64decode(data, validate=True)
        if not 16 <= len(raw) <= MAX_DATA + 16:
            raise ValueError("Invalid editor ciphertext length")
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=self.inbound + seq.to_bytes(8, "big"))
        cipher.update(self.channel)
        value = json.loads(cipher.decrypt_and_verify(raw[:-16], raw[-16:]))
        self.received = seq
        return value
