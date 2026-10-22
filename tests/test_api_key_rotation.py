import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from app.modules.api_keys.api_keys_model import ApiKey
from app.modules.api_keys.api_keys_service import ApiKeyService, hash_api_key


class TestApiKeyRotationAndExpiry(unittest.TestCase):
    def test_expired_key_returns_none_in_resolve(self):
        service = ApiKeyService()
        key_plaintext = "eg_test_expired_key_12345"
        key_hash = hash_api_key(key_plaintext)

        expired_key = ApiKey(
            id=1,
            name="expired-key",
            key_prefix="eg_test",
            key_hash=key_hash,
            tenant_id="tenant-1",
            user_id="user-1",
            is_active=True,
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )

        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = expired_key
        mock_session.execute.return_value = mock_result
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__enter__.return_value = mock_session

        service._session_factory = mock_session_factory

        identity = service.resolve_api_key(key_plaintext)
        self.assertIsNone(identity)

    def test_valid_unexpired_key_resolves(self):
        service = ApiKeyService()
        key_plaintext = "eg_test_valid_key_12345"
        key_hash = hash_api_key(key_plaintext)

        valid_key = ApiKey(
            id=2,
            name="valid-key",
            key_prefix="eg_test",
            key_hash=key_hash,
            tenant_id="tenant-1",
            user_id="user-1",
            is_active=True,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )

        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = valid_key
        mock_session.execute.return_value = mock_result
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__enter__.return_value = mock_session

        service._session_factory = mock_session_factory

        identity = service.resolve_api_key(key_plaintext)
        self.assertIsNotNone(identity)
        self.assertEqual(identity.tenant_id, "tenant-1")

    def test_rotate_api_key_creates_new_and_sets_old_expiry(self):
        service = ApiKeyService()
        old_key = ApiKey(
            id=10,
            name="primary-key",
            key_prefix="eg_primary",
            key_hash="hash-10",
            tenant_id="tenant-rot",
            user_id="user-rot",
            is_active=True,
            expires_at=None,
        )

        mock_session = MagicMock()
        mock_session.get.return_value = old_key
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__enter__.return_value = mock_session
        service._session_factory = mock_session_factory

        res = service.rotate_api_key(10, grace_hours=48)
        self.assertIsNotNone(res)
        new_record, plaintext, updated_old_record = res

        self.assertTrue(plaintext.startswith("eg_"))
        self.assertEqual(new_record.tenant_id, "tenant-rot")
        self.assertIsNone(new_record.expires_at)
        self.assertIsNotNone(updated_old_record.expires_at)
        self.assertGreater(
            updated_old_record.expires_at, datetime.now(UTC) + timedelta(hours=47)
        )


if __name__ == "__main__":
    unittest.main()
