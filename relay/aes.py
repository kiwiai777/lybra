"""Pure-stdlib AES (FIPS-197) for decrypting channel attachments.

Why here: Python's standard library has no AES, and the relay must not add runtime dependencies
(gate core is stdlib-only; relay follows the same rule). Two channel formats need it:

- WeCom smart bot (long connection): AES-256-CBC, IV = first 16 bytes of the key, PKCS#7 padded to a
  multiple of 32 bytes (developer.work.weixin.qq.com/document/path/100719 and /101463).
- Weixin ClawBot (iLink CDN media): AES-128-ECB with PKCS#7 padding to 16 bytes
  (github.com/Tencent/openclaw-weixin docs/protocol_zh_CN.md, "CDN 媒体流程").

Table-driven (T-table) implementation; encryption is included because test fixtures build encrypted
attachments with it, and known-answer vectors (FIPS-197 appendix C, NIST SP 800-38A) pin both directions.
"""
from __future__ import annotations

import struct

BLOCK = 16


def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x11B) if a & 0x100 else a


def _gmul(a: int, b: int) -> int:
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


def _build_tables() -> tuple[list[int], list[int], list[list[int]], list[list[int]]]:
    sbox = [0] * 256
    for x in range(256):
        inv = 0
        if x:
            # multiplicative inverse in GF(2^8): x^254
            inv, base, exp = 1, x, 254
            while exp:
                if exp & 1:
                    inv = _gmul(inv, base)
                base = _gmul(base, base)
                exp >>= 1
        s = inv
        for shift in (1, 2, 3, 4):
            s ^= ((inv << shift) | (inv >> (8 - shift))) & 0xFF
        sbox[x] = s ^ 0x63
    inv_sbox = [0] * 256
    for x, s in enumerate(sbox):
        inv_sbox[s] = x

    def rotr(w: int, n: int) -> int:
        return ((w >> n) | (w << (32 - n))) & 0xFFFFFFFF

    te0 = [(_gmul(s, 2) << 24) | (s << 16) | (s << 8) | _gmul(s, 3) for s in sbox]
    td0 = [(_gmul(s, 14) << 24) | (_gmul(s, 9) << 16) | (_gmul(s, 13) << 8) | _gmul(s, 11) for s in inv_sbox]
    te = [te0] + [[rotr(w, 8 * k) for w in te0] for k in (1, 2, 3)]
    td = [td0] + [[rotr(w, 8 * k) for w in td0] for k in (1, 2, 3)]
    return sbox, inv_sbox, te, td


_S, _SI, _TE, _TD = _build_tables()


class AESError(ValueError):
    """Bad key length, bad ciphertext length or bad padding (wrong key / corrupted data)."""


def _expand_key(key: bytes) -> tuple[list[int], int]:
    if len(key) not in (16, 24, 32):
        raise AESError(f"AES key must be 16/24/32 bytes, got {len(key)}")
    nk = len(key) // 4
    nr = nk + 6
    w = list(struct.unpack(f">{nk}I", key))
    rcon = 1
    for i in range(nk, 4 * (nr + 1)):
        t = w[i - 1]
        if i % nk == 0:
            t = ((t << 8) | (t >> 24)) & 0xFFFFFFFF
            t = (_S[t >> 24] << 24) | (_S[(t >> 16) & 255] << 16) | (_S[(t >> 8) & 255] << 8) | _S[t & 255]
            t ^= rcon << 24
            rcon = _xtime(rcon)
        elif nk > 6 and i % nk == 4:
            t = (_S[t >> 24] << 24) | (_S[(t >> 16) & 255] << 16) | (_S[(t >> 8) & 255] << 8) | _S[t & 255]
        w.append(w[i - nk] ^ t)
    return w, nr


def _decrypt_schedule(ek: list[int], nr: int) -> list[int]:
    td0, td1, td2, td3 = _TD
    dk: list[int] = []
    for r in range(nr + 1):
        words = ek[4 * (nr - r): 4 * (nr - r) + 4]
        if 0 < r < nr:
            words = [td0[_S[x >> 24]] ^ td1[_S[(x >> 16) & 255]] ^ td2[_S[(x >> 8) & 255]] ^ td3[_S[x & 255]] for x in words]
        dk.extend(words)
    return dk


class AES:
    """One expanded key; block-level encrypt / decrypt."""

    def __init__(self, key: bytes) -> None:
        self._ek, self._nr = _expand_key(bytes(key))
        self._dk = _decrypt_schedule(self._ek, self._nr)

    def encrypt_block(self, block: bytes) -> bytes:
        te0, te1, te2, te3 = _TE
        rk, nr, sb = self._ek, self._nr, _S
        s0, s1, s2, s3 = struct.unpack(">4I", block)
        s0 ^= rk[0]
        s1 ^= rk[1]
        s2 ^= rk[2]
        s3 ^= rk[3]
        for r in range(1, nr):
            k = 4 * r
            t0 = te0[s0 >> 24] ^ te1[(s1 >> 16) & 255] ^ te2[(s2 >> 8) & 255] ^ te3[s3 & 255] ^ rk[k]
            t1 = te0[s1 >> 24] ^ te1[(s2 >> 16) & 255] ^ te2[(s3 >> 8) & 255] ^ te3[s0 & 255] ^ rk[k + 1]
            t2 = te0[s2 >> 24] ^ te1[(s3 >> 16) & 255] ^ te2[(s0 >> 8) & 255] ^ te3[s1 & 255] ^ rk[k + 2]
            t3 = te0[s3 >> 24] ^ te1[(s0 >> 16) & 255] ^ te2[(s1 >> 8) & 255] ^ te3[s2 & 255] ^ rk[k + 3]
            s0, s1, s2, s3 = t0, t1, t2, t3
        k = 4 * nr
        o0 = ((sb[s0 >> 24] << 24) | (sb[(s1 >> 16) & 255] << 16) | (sb[(s2 >> 8) & 255] << 8) | sb[s3 & 255]) ^ rk[k]
        o1 = ((sb[s1 >> 24] << 24) | (sb[(s2 >> 16) & 255] << 16) | (sb[(s3 >> 8) & 255] << 8) | sb[s0 & 255]) ^ rk[k + 1]
        o2 = ((sb[s2 >> 24] << 24) | (sb[(s3 >> 16) & 255] << 16) | (sb[(s0 >> 8) & 255] << 8) | sb[s1 & 255]) ^ rk[k + 2]
        o3 = ((sb[s3 >> 24] << 24) | (sb[(s0 >> 16) & 255] << 16) | (sb[(s1 >> 8) & 255] << 8) | sb[s2 & 255]) ^ rk[k + 3]
        return struct.pack(">4I", o0, o1, o2, o3)

    def decrypt_block(self, block: bytes) -> bytes:
        td0, td1, td2, td3 = _TD
        rk, nr, si = self._dk, self._nr, _SI
        s0, s1, s2, s3 = struct.unpack(">4I", block)
        s0 ^= rk[0]
        s1 ^= rk[1]
        s2 ^= rk[2]
        s3 ^= rk[3]
        for r in range(1, nr):
            k = 4 * r
            t0 = td0[s0 >> 24] ^ td1[(s3 >> 16) & 255] ^ td2[(s2 >> 8) & 255] ^ td3[s1 & 255] ^ rk[k]
            t1 = td0[s1 >> 24] ^ td1[(s0 >> 16) & 255] ^ td2[(s3 >> 8) & 255] ^ td3[s2 & 255] ^ rk[k + 1]
            t2 = td0[s2 >> 24] ^ td1[(s1 >> 16) & 255] ^ td2[(s0 >> 8) & 255] ^ td3[s3 & 255] ^ rk[k + 2]
            t3 = td0[s3 >> 24] ^ td1[(s2 >> 16) & 255] ^ td2[(s1 >> 8) & 255] ^ td3[s0 & 255] ^ rk[k + 3]
            s0, s1, s2, s3 = t0, t1, t2, t3
        k = 4 * nr
        o0 = ((si[s0 >> 24] << 24) | (si[(s3 >> 16) & 255] << 16) | (si[(s2 >> 8) & 255] << 8) | si[s1 & 255]) ^ rk[k]
        o1 = ((si[s1 >> 24] << 24) | (si[(s0 >> 16) & 255] << 16) | (si[(s3 >> 8) & 255] << 8) | si[s2 & 255]) ^ rk[k + 1]
        o2 = ((si[s2 >> 24] << 24) | (si[(s1 >> 16) & 255] << 16) | (si[(s0 >> 8) & 255] << 8) | si[s3 & 255]) ^ rk[k + 2]
        o3 = ((si[s3 >> 24] << 24) | (si[(s2 >> 16) & 255] << 16) | (si[(s1 >> 8) & 255] << 8) | si[s0 & 255]) ^ rk[k + 3]
        return struct.pack(">4I", o0, o1, o2, o3)


def pkcs7_pad(data: bytes, block_size: int) -> bytes:
    n = block_size - (len(data) % block_size)
    return data + bytes([n]) * n


def pkcs7_unpad(data: bytes, block_size: int) -> bytes:
    if not data or len(data) % BLOCK:
        raise AESError("ciphertext length is not a multiple of 16")
    n = data[-1]
    if n < 1 or n > block_size or data[-n:] != bytes([n]) * n:
        raise AESError("bad PKCS#7 padding (wrong key or corrupted data)")
    return data[:-n]


def _check_blocks(data: bytes) -> None:
    if not data or len(data) % BLOCK:
        raise AESError(f"ciphertext length {len(data)} is not a positive multiple of 16")


def ecb_decrypt_raw(key: bytes, data: bytes) -> bytes:
    _check_blocks(data)
    aes = AES(key)
    return b"".join(aes.decrypt_block(data[i:i + BLOCK]) for i in range(0, len(data), BLOCK))


def ecb_encrypt_raw(key: bytes, data: bytes) -> bytes:
    _check_blocks(data)
    aes = AES(key)
    return b"".join(aes.encrypt_block(data[i:i + BLOCK]) for i in range(0, len(data), BLOCK))


def cbc_decrypt_raw(key: bytes, iv: bytes, data: bytes) -> bytes:
    _check_blocks(data)
    if len(iv) != BLOCK:
        raise AESError("IV must be 16 bytes")
    aes = AES(key)
    out = []
    prev = iv
    for i in range(0, len(data), BLOCK):
        block = data[i:i + BLOCK]
        plain = aes.decrypt_block(block)
        out.append(bytes(a ^ b for a, b in zip(plain, prev)))
        prev = block
    return b"".join(out)


def cbc_encrypt_raw(key: bytes, iv: bytes, data: bytes) -> bytes:
    _check_blocks(data)
    if len(iv) != BLOCK:
        raise AESError("IV must be 16 bytes")
    aes = AES(key)
    out = []
    prev = iv
    for i in range(0, len(data), BLOCK):
        prev = aes.encrypt_block(bytes(a ^ b for a, b in zip(data[i:i + BLOCK], prev)))
        out.append(prev)
    return b"".join(out)


# ---- channel formats -------------------------------------------------------------------------------

def wecom_decrypt(key: bytes, data: bytes) -> bytes:
    """WeCom smart bot media: AES-256-CBC, IV = key[:16], PKCS#7 padded to a multiple of 32 bytes."""
    if len(key) != 32:
        raise AESError(f"WeCom media key must decode to 32 bytes, got {len(key)}")
    return pkcs7_unpad(cbc_decrypt_raw(key, key[:16], data), 32)


def wecom_encrypt(key: bytes, data: bytes) -> bytes:
    if len(key) != 32:
        raise AESError(f"WeCom media key must be 32 bytes, got {len(key)}")
    return cbc_encrypt_raw(key, key[:16], pkcs7_pad(data, 32))


def ilink_decrypt(key: bytes, data: bytes) -> bytes:
    """Weixin iLink CDN media: AES-128-ECB, PKCS#7 padded to 16 bytes."""
    if len(key) != 16:
        raise AESError(f"iLink media key must be 16 bytes, got {len(key)}")
    return pkcs7_unpad(ecb_decrypt_raw(key, data), 16)


def ilink_encrypt(key: bytes, data: bytes) -> bytes:
    if len(key) != 16:
        raise AESError(f"iLink media key must be 16 bytes, got {len(key)}")
    return ecb_encrypt_raw(key, pkcs7_pad(data, 16))
