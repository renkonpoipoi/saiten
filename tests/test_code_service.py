from __future__ import annotations

from app.services.code_service import (
    HOST_CODE_PREFIX,
    SCORER_CODE_PREFIX,
    generate_host_code,
    generate_scorer_code,
    hash_code,
    verify_code,
)


def test_generate_host_code_has_prefix_and_is_random():
    a = generate_host_code()
    b = generate_host_code()
    assert a.startswith(HOST_CODE_PREFIX)
    assert b.startswith(HOST_CODE_PREFIX)
    assert a != b


def test_generate_scorer_code_has_prefix_and_is_random():
    a = generate_scorer_code()
    b = generate_scorer_code()
    assert a.startswith(SCORER_CODE_PREFIX)
    assert b.startswith(SCORER_CODE_PREFIX)
    assert a != b


def test_hash_code_is_deterministic():
    code = generate_scorer_code()
    assert hash_code(code) == hash_code(code)


def test_verify_code_matches_only_original_plaintext():
    code = generate_scorer_code()
    other = generate_scorer_code()
    digest = hash_code(code)

    assert verify_code(code, digest) is True
    assert verify_code(other, digest) is False


def test_hash_is_sha256_hex_digest_length():
    digest = hash_code("anything")
    assert len(digest) == 64
    int(digest, 16)  # 16進文字列として妥当であることの確認
