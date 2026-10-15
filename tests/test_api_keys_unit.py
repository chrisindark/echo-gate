"""Unit tests for API key hashing/identity helpers (no database required)."""

from app.modules.api_keys.api_keys_service import ApiKeyService, hash_api_key


def test_hash_api_key_is_deterministic_and_not_reversible_prefix():
    key = "eg_example_key_value"
    digest = hash_api_key(key)
    assert digest == hash_api_key(key)
    assert len(digest) == 64
    assert key not in digest


def test_hash_api_key_differs_per_key():
    assert hash_api_key("eg_one") != hash_api_key("eg_two")


def test_generate_key_has_prefix_and_entropy():
    first = ApiKeyService.generate_key()
    second = ApiKeyService.generate_key()
    assert first.startswith("eg_")
    assert first != second
    assert len(first) > len("eg_") + 20


def test_resolve_returns_none_for_empty_key():
    assert ApiKeyService().resolve_api_key("") is None
