"""AIPOS-F290 — the relay's stdlib AES (attachment decryption) against published known-answer vectors.

FIPS-197 appendix C.1 / C.3 (single block, AES-128 / AES-256) and NIST SP 800-38A F.1.1 (ECB-AES128) / F.2.5 (CBC-AES256),
both directions; plus the two channel formats (WeCom AES-256-CBC with 32-byte PKCS#7, iLink AES-128-ECB) round-trip and
wrong-key / truncated input fail closed (AESError, never garbage output).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay import aes  # noqa: E402

PT = bytes.fromhex("00112233445566778899aabbccddeeff")
SP_PT = bytes.fromhex("6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51"
                      "30c81c46a35ce411e5fbc1191a0a52eff69f2445df4f9b17ad2b417be66c3710")


def test_fips197_single_block():
    for key_len, expect in ((16, "69c4e0d86a7b0430d8cdb78070b4c55a"), (32, "8ea2b7ca516745bfeafc49904b496089")):
        cipher = aes.AES(bytes(range(key_len)))
        assert cipher.encrypt_block(PT).hex() == expect
        assert cipher.decrypt_block(bytes.fromhex(expect)) == PT


def test_sp800_38a_ecb_aes128():
    key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    ct = bytes.fromhex("3ad77bb40d7a3660a89ecaf32466ef97f5d3d58503b9699de785895a96fdbaaf"
                       "43b1cd7f598ece23881b00e3ed0306887b0c785e27e8ad3f8223207104725dd4")
    assert aes.ecb_encrypt_raw(key, SP_PT) == ct
    assert aes.ecb_decrypt_raw(key, ct) == SP_PT


def test_sp800_38a_cbc_aes256():
    key = bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
    iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    ct = bytes.fromhex("f58c4c04d6e5f1ba779eabfb5f7bfbd69cfc4e967edb808d679f777bc6702c7d"
                       "39f23369a9d9bacfa530e26304231461b2eb05e2c39be9fcda6c19078c6a9d1b")
    assert aes.cbc_encrypt_raw(key, iv, SP_PT) == ct
    assert aes.cbc_decrypt_raw(key, iv, ct) == SP_PT


@pytest.mark.parametrize("size", [0, 1, 15, 16, 31, 32, 33, 1000])
def test_channel_formats_round_trip(size):
    data = os.urandom(size)
    k32, k16 = os.urandom(32), os.urandom(16)
    wecom = aes.wecom_encrypt(k32, data)
    assert len(wecom) % 32 == 0 and len(wecom) > size     # padded to a multiple of 32 (WeCom doc)
    assert aes.wecom_decrypt(k32, wecom) == data
    ilink = aes.ilink_encrypt(k16, data)
    assert len(ilink) % 16 == 0 and len(ilink) > size
    assert aes.ilink_decrypt(k16, ilink) == data


def test_wrong_key_or_truncated_input_fails_closed():
    data = b"attachment payload " * 10     # fixed inputs: the wrong-key outcome is deterministic, not 1-in-256 flaky
    k32 = bytes(range(32))
    wrong = bytes(32 - 1) + b"\x01"
    with pytest.raises(aes.AESError):
        aes.wecom_decrypt(wrong, aes.wecom_encrypt(k32, data))
    with pytest.raises(aes.AESError):
        aes.wecom_decrypt(k32, aes.wecom_encrypt(k32, data)[:-5])
    with pytest.raises(aes.AESError):
        aes.ilink_decrypt(bytes(32), b"x" * 16)
    with pytest.raises(aes.AESError):
        aes.AES(b"short")
