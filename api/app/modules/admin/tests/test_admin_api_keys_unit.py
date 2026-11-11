import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.admin.admin_api_keys_model import AdminApiKey
from app.modules.admin.admin_api_keys_service import (
    ADMIN_WILDCARD_SCOPE,
    AdminApiKeyService,
    AdminKeyIdentity,
    InvalidScopeError,
    normalize_scopes,
    scopes_from_text,
    scopes_to_text,
)
from app.modules.api_keys.api_keys_service import hash_api_key


class TestAdminApiKeysUnit(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.service = AdminApiKeyService(session_factory=self.Session)

    def tearDown(self):
        self.engine.dispose()

    def test_normalize_scopes_valid(self):
        res = normalize_scopes("quota:read, quota:write")
        self.assertEqual(res, ["quota:read", "quota:write"])

        res2 = normalize_scopes(["quota:read", "quota:read", "quota:write"])
        self.assertEqual(res2, ["quota:read", "quota:write"])

        # Wildcard stands alone
        res3 = normalize_scopes(["quota:read", ADMIN_WILDCARD_SCOPE])
        self.assertEqual(res3, [ADMIN_WILDCARD_SCOPE])

    def test_normalize_scopes_invalid(self):
        with self.assertRaises(InvalidScopeError):
            normalize_scopes("")

        with self.assertRaises(InvalidScopeError):
            normalize_scopes([])

        with self.assertRaises(InvalidScopeError):
            normalize_scopes("unknown:scope, fake:scope")

    def test_scopes_serialization(self):
        self.assertEqual(scopes_to_text(["a", "b"]), "a,b")
        self.assertEqual(scopes_from_text("a, b, , c"), {"a", "b", "c"})
        self.assertEqual(scopes_from_text(""), set())
        self.assertEqual(scopes_from_text(None), set())

    def test_admin_key_identity(self):
        ident = AdminKeyIdentity(
            key_id=1,
            name="test-key",
            scopes=frozenset(["quota:write"]),
            expires_at=None,
        )
        self.assertFalse(ident.is_expired())
        self.assertTrue(ident.has_scope("quota:write"))
        self.assertTrue(ident.has_scope("quota:read"))  # write implies read
        self.assertFalse(ident.has_scope("cache:read"))

        # Wildcard identity
        wildcard_ident = AdminKeyIdentity(
            key_id=2,
            name="root",
            scopes=frozenset([ADMIN_WILDCARD_SCOPE]),
        )
        self.assertTrue(wildcard_ident.has_scope("anything:write"))
        self.assertTrue(wildcard_ident.has_scope("random:read"))

        # Expiry checks
        future = datetime.now(UTC) + timedelta(hours=1)
        past = datetime.now(UTC) - timedelta(hours=1)
        ident_future = AdminKeyIdentity(
            key_id=3, name="fut", scopes=frozenset(), expires_at=future
        )
        ident_past = AdminKeyIdentity(
            key_id=4, name="past", scopes=frozenset(), expires_at=past
        )
        self.assertFalse(ident_future.is_expired())
        self.assertTrue(ident_past.is_expired())

    def test_create_and_list_admin_keys(self):
        rec, plaintext = self.service.create_admin_key(
            name="super-admin",
            scopes=["quota:read", "keys:write"],
            expires_in_days=30,
        )
        self.assertIsNotNone(rec.id)
        self.assertTrue(plaintext.startswith(self.service.generate_key()[:4]))

        keys = self.service.list_admin_keys()
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0].name, "super-admin")

        # Revoke key
        revoked = self.service.revoke_admin_key(rec.id)
        self.assertTrue(revoked)
        self.assertFalse(self.service.revoke_admin_key(999999))

        # Check that revoked key no longer resolves
        self.assertIsNone(self.service.resolve_admin_key(plaintext))

    def test_resolve_admin_key_lifecycle(self):
        raw_key = "admin-secret-key-12345"
        key_hash = hash_api_key(raw_key)

        with self.Session() as s:
            record = AdminApiKey(
                name="valid-admin",
                key_prefix="admin-sec",
                key_hash=key_hash,
                scopes="quota:write,cache:read",
                is_active=True,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            s.add(record)
            s.commit()

        # First resolution (uncached)
        ident = self.service.resolve_admin_key(raw_key)
        self.assertIsNotNone(ident)
        self.assertEqual(ident.name, "valid-admin")
        self.assertTrue(ident.has_scope("quota:write"))

        # Second resolution (cached)
        cached_ident = self.service.resolve_admin_key(raw_key)
        self.assertIsNotNone(cached_ident)
        self.assertEqual(cached_ident.name, "valid-admin")

        # Empty plaintext returns None
        self.assertIsNone(self.service.resolve_admin_key(""))

        # Clear cache and resolve nonexistent key
        self.service._clear_cache()
        none_ident = self.service.resolve_admin_key("nonexistent-key")
        self.assertIsNone(none_ident)

        # Negative cache hit
        none_cached = self.service.resolve_admin_key("nonexistent-key")
        self.assertIsNone(none_cached)

    def test_resolve_admin_key_expired_and_inactive(self):
        raw_exp = "admin-expired-key"
        with self.Session() as s:
            s.add(
                AdminApiKey(
                    name="exp",
                    key_prefix="admin-exp",
                    key_hash=hash_api_key(raw_exp),
                    scopes="admin:*",
                    is_active=True,
                    expires_at=datetime.now(UTC) - timedelta(days=1),
                )
            )
            s.commit()

        ident = self.service.resolve_admin_key(raw_exp)
        self.assertIsNone(ident)

        raw_inact = "admin-inactive-key"
        with self.Session() as s:
            s.add(
                AdminApiKey(
                    name="inact",
                    key_prefix="admin-ina",
                    key_hash=hash_api_key(raw_inact),
                    scopes="admin:*",
                    is_active=False,
                )
            )
            s.commit()

        ident_inact = self.service.resolve_admin_key(raw_inact)
        self.assertIsNone(ident_inact)

    def test_touch_last_used_and_error_handling(self):
        raw_key = "admin-touch-key"
        with self.Session() as s:
            rec = AdminApiKey(
                name="touch-test",
                key_prefix="admin-tou",
                key_hash=hash_api_key(raw_key),
                scopes="quota:read",
                is_active=True,
                last_used_at=None,
            )
            s.add(rec)
            s.commit()

        # Resolving updates last_used_at
        self.service.resolve_admin_key(raw_key)

        with self.Session() as s:
            updated = (
                s.query(AdminApiKey).filter_by(key_hash=hash_api_key(raw_key)).first()
            )
            self.assertIsNotNone(updated.last_used_at)

        # Immediate re-touch is throttled (less than 300 seconds)
        mock_session = MagicMock()
        mock_rec = MagicMock()
        mock_rec.last_used_at = datetime.now(UTC)
        self.service._touch_last_used(mock_session, mock_rec)
        mock_session.commit.assert_not_called()

        # Commit error in _touch_last_used is caught and rolled back safely
        mock_session_err = MagicMock()
        mock_session_err.commit.side_effect = RuntimeError("DB write error")
        mock_rec_stale = MagicMock()
        mock_rec_stale.last_used_at = datetime.now(UTC) - timedelta(seconds=500)
        self.service._touch_last_used(mock_session_err, mock_rec_stale)
        mock_session_err.rollback.assert_called_once()

    def test_resolve_key_db_exception_fails_open_safely(self):
        service = AdminApiKeyService(
            session_factory=MagicMock(side_effect=RuntimeError("DB down"))
        )
        ident = service.resolve_admin_key("any-key")
        self.assertIsNone(ident)

    def test_session_init_variants(self):
        # Session directly passed
        with self.Session() as s:
            svc = AdminApiKeyService(session_factory=s)
            self.assertEqual(svc._db_session, s)
