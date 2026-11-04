"""Bridge v1 wire cryptography. No TLS-only or legacy shared-relay fallback."""

import base64
import hashlib
import ipaddress
import json
import re
import secrets
import struct
import uuid
from urllib.parse import urlsplit

CONTROL_NOISE = b"Noise_KK_25519_ChaChaPoly_BLAKE2s"
PREVIEW_NOISE = b"Noise_K_25519_ChaChaPoly_BLAKE2s"
MAX_CHUNK = 32768
MAX_PREVIEW = 1024


def exact_origin(value):
    if (not isinstance(value, str) or not value.isascii() or len(value) > 2048
            or any(c.isspace() or ord(c) < 32 or c == '\\' for c in value)):
        raise ValueError("an exact HTTPS origin is required")
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path or parts.query or parts.fragment
            or not value.isascii() or len(value) > 2048 or parts.netloc != parts.netloc.lower()):
        raise ValueError("an exact HTTPS origin without a path is required")
    host = parts.hostname
    if ':' in host:
        host = '[' + ipaddress.IPv6Address(host).compressed + ']'
    else:
        try:
            host = str(ipaddress.IPv4Address(host))
        except ValueError:
            # WHATWG URL parsers interpret a numeric final label as IPv4. Do not
            # let Python accept aliases that the Rust client would normalize.
            last = host.rstrip('.').rsplit('.', 1)[-1]
            if (re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)', last)
                    or not re.fullmatch(r'[a-z0-9_.-]+', host)):
                raise ValueError("non-canonical server host") from None
    port = parts.port
    authority = host + (':' + str(port) if port is not None else '')
    # Never silently normalize an address used to select an OS-vault credential.
    if port == 443 or port == 0 or value != "https://" + authority:
        raise ValueError("non-canonical server origin")
    return value


def _origin_bytes(origin):
    raw = exact_origin(origin).encode("ascii")
    return struct.pack("!H", len(raw)) + raw


def public_key(private):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    if len(private) != 32:
        raise ValueError("invalid key size")
    return X25519PrivateKey.from_private_bytes(private).public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw)


def new_key():
    return secrets.token_bytes(32)


def decode_key(encoded):
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) != 32 or not any(raw):
        raise ValueError("invalid X25519 public key")
    return raw


def control_prologue(origin, gateway, binding, channel, desktop_key, mobile_key):
    if len(desktop_key) != 32 or len(mobile_key) != 32:
        raise ValueError("invalid key size")
    return (b"bridge/control/v1\0" + _origin_bytes(origin)
            + uuid.UUID(gateway).bytes + uuid.UUID(binding).bytes + uuid.UUID(channel).bytes
            + desktop_key + mobile_key)


def pairing_safety_code(origin, gateway, binding, private, remote, desktop, mobile,
                        desktop_preview, mobile_preview):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    if any(len(key) != 32 for key in (private, remote, desktop, mobile, desktop_preview, mobile_preview)):
        raise ValueError('invalid pairing key')
    shared = X25519PrivateKey.from_private_bytes(private).exchange(X25519PublicKey.from_public_bytes(remote))
    raw = (b'bridge/pairing-safety/v1\0' + _origin_bytes(origin)
           + uuid.UUID(gateway).bytes + uuid.UUID(binding).bytes
           + shared + desktop + mobile + desktop_preview + mobile_preview)
    code = hashlib.sha256(raw).hexdigest().upper()
    return ' '.join(code[index:index + 8] for index in range(0, 64, 8))


def preview_prologue(origin, envelope):
    if envelope["v"] != 1:
        raise ValueError("unsupported preview protocol")
    return (b"bridge/preview/v1\0" + _origin_bytes(origin)
            + uuid.UUID(envelope["notification_id"]).bytes
            + uuid.UUID(envelope["binding_id"]).bytes
            + struct.pack("!q", envelope["expires_at"]))


def _connection(pattern, private, remote, prologue, initiator, _test_ephemeral=None):
    from noise.connection import Keypair, NoiseConnection
    if len(private) != 32 or len(remote) != 32:
        raise ValueError("invalid key size")
    connection = NoiseConnection.from_name(pattern)
    connection.set_as_initiator() if initiator else connection.set_as_responder()
    connection.set_prologue(prologue)
    connection.set_keypair_from_private_bytes(Keypair.STATIC, private)
    connection.set_keypair_from_public_bytes(Keypair.REMOTE_STATIC, remote)
    if _test_ephemeral is not None:
        connection.set_keypair_from_private_bytes(Keypair.EPHEMERAL, _test_ephemeral)
    connection.start_handshake()
    return connection


class ControlCipher:
    """A failed or out-of-order record permanently poisons this channel."""

    def __init__(self, private, remote, prologue, initiator=False, *, _test_ephemeral=None):
        self._noise = _connection(CONTROL_NOISE, private, remote, prologue, initiator,
                                  _test_ephemeral)
        self._failed = False

    @property
    def ready(self):
        return not self._failed and self._noise.handshake_finished

    def _call(self, method, data):
        if self._failed:
            raise ValueError("channel closed")
        try:
            return bytes(method(data))
        except Exception:
            self._failed = True
            raise ValueError("record authentication failed") from None

    def handshake_write(self):
        return self._call(self._noise.write_message, b"")

    def handshake_read(self, data):
        payload = self._call(self._noise.read_message, data)
        if payload:
            self._failed = True
            raise ValueError("early application data is forbidden")

    def encrypt(self, data):
        if not self.ready or len(data) > MAX_CHUNK:
            self._failed = True
            raise ValueError("handshake incomplete or chunk too large")
        return self._call(self._noise.encrypt, data)

    def decrypt(self, data):
        if not self.ready or not 16 <= len(data) <= MAX_CHUNK + 16:
            self._failed = True
            raise ValueError("invalid encrypted chunk")
        return self._call(self._noise.decrypt, data)


def encrypt_preview(origin, private, remote, envelope, plaintext, *, _test_ephemeral=None):
    if len(plaintext) > MAX_PREVIEW:
        raise ValueError("preview too large")
    connection = _connection(PREVIEW_NOISE, private, remote,
                             preview_prologue(origin, envelope), True, _test_ephemeral)
    return base64.b64encode(connection.write_message(plaintext)).decode("ascii")


def decrypt_preview(origin, private, remote, envelope, now):
    if set(envelope) != {"v", "notification_id", "binding_id", "expires_at", "ciphertext"}:
        raise ValueError("invalid preview envelope")
    if not now < envelope["expires_at"] <= now + 3600:
        raise ValueError("expired preview")
    raw = base64.b64decode(envelope["ciphertext"], validate=True)
    if not 48 <= len(raw) <= MAX_PREVIEW + 48:
        raise ValueError("invalid preview size")
    connection = _connection(PREVIEW_NOISE, private, remote,
                             preview_prologue(origin, envelope), False)
    return bytes(connection.read_message(raw))


def encode_frame(kind, binding, channel, payload=b""):
    if kind not in (1, 2, 3) or (kind == 1 and len(payload) != 32) or (
            kind == 2 and not 16 <= len(payload) <= MAX_CHUNK + 16) or (kind == 3 and payload):
        raise ValueError("invalid relay frame")
    return bytes((1, kind)) + uuid.UUID(binding).bytes + uuid.UUID(channel).bytes + payload


def decode_frame(data):
    if len(data) < 34 or data[0] != 1:
        raise ValueError("invalid relay version")
    kind, binding, channel, payload = (data[1], str(uuid.UUID(bytes=data[2:18])),
                                       str(uuid.UUID(bytes=data[18:34])), data[34:])
    encode_frame(kind, binding, channel, payload)
    return kind, binding, channel, payload


def encode_message(value):
    raw = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_CHUNK:
        raise ValueError("message requires chunking")
    return raw
