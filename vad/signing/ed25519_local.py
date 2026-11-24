"""Local Ed25519 DSSE key provider with optional cryptography fast path and RFC 8032 pure-Python fallback."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
from typing import Protocol, runtime_checkable

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False


DEFAULT_KEY_DIR = Path(".vad/keys")
KEY_FILENAME = "dsse_ed25519.json"

# RFC 8032 Section 5.1 Edwards25519 domain parameters
_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_D2 = (2 * _D) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)
_IDENTITY = (0, 1, 1, 0)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    u = (y * y - 1) % _P
    v = (_D * y * y + 1) % _P
    v3 = pow(v, 3, _P)
    v7 = (v3 * v3 * v) % _P
    x = (u * v3 * pow((u * v7) % _P, (_P - 5) // 8, _P)) % _P
    vx2 = (v * x * x) % _P
    if vx2 == u:
        pass
    elif vx2 == (-u) % _P:
        x = (x * _SQRT_M1) % _P
    else:
        return None
    if x == 0 and sign == 1:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_BASE_Y = (4 * pow(5, _P - 2, _P)) % _P
_BASE_X = _recover_x(_BASE_Y, 0)
assert _BASE_X is not None
_BASE_POINT = (_BASE_X, _BASE_Y, 1, (_BASE_X * _BASE_Y) % _P)


def _point_add(
    p1: tuple[int, int, int, int],
    p2: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = ((y1 - x1) * (y2 - x2)) % _P
    b = ((y1 + x1) * (y2 + x2)) % _P
    c = (t1 * _D2 * t2) % _P
    d = (z1 * 2 * z2) % _P
    e = (b - a) % _P
    f = (d - c) % _P
    g = (d + c) % _P
    h = (b + a) % _P
    return ((e * f) % _P, (g * h) % _P, (f * g) % _P, (e * h) % _P)


def _point_double(p: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x1, y1, z1, _ = p
    a = (x1 * x1) % _P
    b = (y1 * y1) % _P
    c = (2 * z1 * z1) % _P
    h = (a + b) % _P
    e = (h - ((x1 + y1) * (x1 + y1)) % _P) % _P
    g = (a - b) % _P
    f = (c + g) % _P
    return ((e * f) % _P, (g * h) % _P, (f * g) % _P, (e * h) % _P)


def _scalar_mult(scalar: int, point: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    result = _IDENTITY
    addend = point
    k = scalar
    while k > 0:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_double(addend)
        k >>= 1
    return result


def _point_equal(p1: tuple[int, int, int, int], p2: tuple[int, int, int, int]) -> bool:
    x1, y1, z1, _ = p1
    x2, y2, z2, _ = p2
    return ((x1 * z2 - x2 * z1) % _P == 0) and ((y1 * z2 - y2 * z1) % _P == 0)


def _encode_point(point: tuple[int, int, int, int]) -> bytes:
    x, y, z, _ = point
    z_inv = pow(z, _P - 2, _P)
    x_aff = (x * z_inv) % _P
    y_aff = (y * z_inv) % _P
    out = bytearray(y_aff.to_bytes(32, "little"))
    out[31] |= (x_aff & 1) << 7
    return bytes(out)


def _decode_point(raw: bytes) -> tuple[int, int, int, int] | None:
    if len(raw) != 32:
        return None
    y_with_sign = int.from_bytes(raw, "little")
    sign = (y_with_sign >> 255) & 1
    y = y_with_sign & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, (x * y) % _P)


def _expand_seed(seed: bytes) -> tuple[int, bytes, bytes]:
    if not isinstance(seed, (bytes, bytearray)) or len(seed) != 32:
        raise ValueError("Ed25519 seed must be exactly 32 bytes")
    digest = hashlib.sha512(bytes(seed)).digest()
    scalar_bytes = bytearray(digest[:32])
    scalar_bytes[0] &= 248
    scalar_bytes[31] = (scalar_bytes[31] & 63) | 64
    scalar = int.from_bytes(scalar_bytes, "little")
    pub_bytes = _encode_point(_scalar_mult(scalar, _BASE_POINT))
    return scalar, digest[32:], pub_bytes


def pure_ed25519_public_key_from_seed(seed: bytes) -> bytes:
    """Derive 32-byte RFC 8032 Ed25519 public key from a 32-byte seed using pure Python."""
    _, _, pub_bytes = _expand_seed(seed)
    return pub_bytes


def pure_ed25519_sign(seed: bytes, message: bytes) -> bytes:
    """Sign a message with a 32-byte Ed25519 seed using pure-Python RFC 8032 math."""
    scalar, prefix, pub_bytes = _expand_seed(seed)
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _L
    r_bytes = _encode_point(_scalar_mult(r, _BASE_POINT))
    k = int.from_bytes(hashlib.sha512(r_bytes + pub_bytes + message).digest(), "little") % _L
    s = (r + k * scalar) % _L
    return r_bytes + s.to_bytes(32, "little")


def pure_ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verify an RFC 8032 Ed25519 signature using pure-Python Edwards25519 math."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a_point = _decode_point(public_key)
    if a_point is None:
        return False
    r_bytes = signature[:32]
    s_bytes = signature[32:]
    r_point = _decode_point(r_bytes)
    if r_point is None:
        return False
    s = int.from_bytes(s_bytes, "little")
    if s >= _L:
        return False
    k = int.from_bytes(hashlib.sha512(r_bytes + public_key + message).digest(), "little") % _L
    lhs = _scalar_mult(s, _BASE_POINT)
    rhs = _point_add(r_point, _scalar_mult(k, a_point))
    return _point_equal(lhs, rhs)


def ed25519_public_key_from_seed(seed: bytes) -> bytes:
    """Derive 32-byte Ed25519 public key from a 32-byte seed."""
    if not isinstance(seed, (bytes, bytearray)) or len(seed) != 32:
        raise ValueError("Ed25519 seed must be exactly 32 bytes")
    if _HAS_CRYPTOGRAPHY:
        priv = Ed25519PrivateKey.from_private_bytes(bytes(seed))
        return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return pure_ed25519_public_key_from_seed(bytes(seed))


def ed25519_sign(seed: bytes, message: bytes) -> bytes:
    """Sign message bytes with a 32-byte Ed25519 seed."""
    if not isinstance(seed, (bytes, bytearray)) or len(seed) != 32:
        raise ValueError("Ed25519 seed must be exactly 32 bytes")
    if _HAS_CRYPTOGRAPHY:
        priv = Ed25519PrivateKey.from_private_bytes(bytes(seed))
        return priv.sign(message)
    return pure_ed25519_sign(bytes(seed), message)


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verify an Ed25519 signature against a 32-byte raw public key."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    if _HAS_CRYPTOGRAPHY:
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
            return True
        except (InvalidSignature, ValueError):
            return False
    return pure_ed25519_verify(public_key, message, signature)


def _default_key_id(public_key: bytes) -> str:
    return f"ed25519-{hashlib.sha256(public_key).hexdigest()[:16]}"


@runtime_checkable
class DSSEKeyProvider(Protocol):
    """Pluggable DSSE signing and verification provider interface."""

    key_id: str
    algorithm: str

    def sign_pae(self, pae: bytes) -> bytes: ...

    def verify_pae(self, pae: bytes, signature: bytes) -> bool: ...


KeyProvider = DSSEKeyProvider


class ExplicitHMACKeyProvider:
    """Explicit HMAC-SHA256 DSSE provider that rejects empty secrets."""

    def __init__(self, key_id: str, secret_key: bytes):
        if not key_id:
            raise ValueError("key_id must not be empty")
        if not isinstance(secret_key, (bytes, bytearray)) or len(secret_key) == 0:
            raise ValueError("secret_key must not be empty")
        self.key_id = key_id
        self.algorithm = "hmac-sha256"
        self._secret_key = bytes(secret_key)

    def sign_pae(self, pae: bytes) -> bytes:
        return hmac.new(self._secret_key, pae, hashlib.sha256).digest()

    def verify_pae(self, pae: bytes, signature: bytes) -> bool:
        if not isinstance(signature, (bytes, bytearray)):
            return False
        expected = self.sign_pae(pae)
        return hmac.compare_digest(expected, bytes(signature))


class LocalEd25519KeyProvider:
    """Local Ed25519 keypair manager backed by `.vad/keys/dsse_ed25519.json`."""

    def __init__(
        self,
        key_dir: str | Path | None = None,
        *,
        key_id: str | None = None,
    ):
        self.algorithm = "ed25519"
        self.key_dir: Path | None = Path(key_dir) if key_dir is not None else DEFAULT_KEY_DIR
        self.key_file: Path | None = self.key_dir / KEY_FILENAME
        if self.key_file.exists():
            loaded_id, seed, pub = self._load_keyfile(self.key_file)
            self.key_id = loaded_id
            self._seed = seed
            self._public_key = pub
        else:
            seed = secrets.token_bytes(32)
            pub = ed25519_public_key_from_seed(seed)
            self.key_id = key_id or _default_key_id(pub)
            self._seed = seed
            self._public_key = pub
            self._save_keyfile(self.key_file, self.key_id, self._seed, self._public_key)

    @classmethod
    def from_seed(
        cls,
        seed: bytes,
        key_id: str | None = None,
        key_dir: str | Path | None = None,
    ) -> LocalEd25519KeyProvider:
        """Create a deterministic Ed25519 provider from a 32-byte seed without touching `.vad/keys` unless `key_dir` is given."""
        if not isinstance(seed, (bytes, bytearray)) or len(seed) != 32:
            raise ValueError("Ed25519 seed must be exactly 32 bytes")
        seed_bytes = bytes(seed)
        pub = ed25519_public_key_from_seed(seed_bytes)
        resolved_id = key_id or _default_key_id(pub)
        instance = cls.__new__(cls)
        instance.algorithm = "ed25519"
        instance.key_id = resolved_id
        instance._seed = seed_bytes
        instance._public_key = pub
        if key_dir is not None:
            instance.key_dir = Path(key_dir)
            instance.key_file = instance.key_dir / KEY_FILENAME
            cls._save_keyfile(instance.key_file, resolved_id, seed_bytes, pub)
        else:
            instance.key_dir = None
            instance.key_file = None
        return instance

    @property
    def public_key(self) -> bytes:
        return self._public_key

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(self._public_key).decode("ascii")

    def rotate(self) -> LocalEd25519KeyProvider:
        """Generate a fresh Ed25519 keypair, persist it if disk-backed, and return the new provider."""
        new_seed = secrets.token_bytes(32)
        new_pub = ed25519_public_key_from_seed(new_seed)
        new_id = _default_key_id(new_pub)
        instance = self.__class__.__new__(self.__class__)
        instance.algorithm = "ed25519"
        instance.key_id = new_id
        instance._seed = new_seed
        instance._public_key = new_pub
        instance.key_dir = self.key_dir
        instance.key_file = self.key_file
        if instance.key_file is not None:
            self._save_keyfile(instance.key_file, new_id, new_seed, new_pub)
        return instance

    def sign_pae(self, pae: bytes) -> bytes:
        return ed25519_sign(self._seed, pae)

    def verify_pae(self, pae: bytes, signature: bytes) -> bool:
        return ed25519_verify(self._public_key, pae, bytes(signature))

    @staticmethod
    def _save_keyfile(path: Path, key_id: str, seed: bytes, public_key: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "algorithm": "ed25519",
            "key_id": key_id,
            "private_key": base64.b64encode(seed).decode("ascii"),
            "public_key": base64.b64encode(public_key).decode("ascii"),
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if os.name == "posix":
            os.chmod(path, 0o600)

    @staticmethod
    def _load_keyfile(path: Path) -> tuple[str, bytes, bytes]:
        data = json.loads(path.read_text(encoding="utf-8"))
        key_id = data["key_id"]
        seed = base64.b64decode(data["private_key"], validate=True)
        pub = base64.b64decode(data["public_key"], validate=True)
        if len(seed) != 32 or len(pub) != 32:
            raise ValueError("Invalid Ed25519 key lengths in keyfile")
        if ed25519_public_key_from_seed(seed) != pub:
            raise ValueError("Corrupted Ed25519 keyfile: public_key does not match private_key")
        return key_id, seed, pub
