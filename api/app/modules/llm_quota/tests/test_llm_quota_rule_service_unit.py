import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.llm_quota.llm_quota_rule_service import (
    LlmQuotaRuleService,
    UnauthenticatedAdminAccessError,
)
from app.modules.llm_quota.llm_quota_schema import (
    LlmQuotaRuleCreate,
    LlmQuotaRuleUpdate,
)


class TestLlmQuotaRuleServiceUnit(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.service = LlmQuotaRuleService(session_factory=self.Session)
        self.admin = AdminKeyIdentity(
            key_id=1, name="admin", scopes=frozenset({"quota:read", "quota:write"})
        )

    def tearDown(self):
        self.Session.close_all()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_init_session_injection(self):
        with self.Session() as s:
            svc_session = LlmQuotaRuleService(session_factory=s)
            self.assertIs(svc_session._db_session, s)

            from unittest.mock import MagicMock, patch

            with patch(
                "app.modules.llm_quota.llm_quota_rule_service.SessionLocal"
            ) as mock_sl:
                mock_s = MagicMock()
                mock_sl.return_value.__enter__.return_value = mock_s
                svc_default = LlmQuotaRuleService()
                svc_default._session_factory = None
                with svc_default._get_session(self.admin) as opened:
                    self.assertIs(opened, mock_s)

    def test_unauthenticated_admin_access_error(self):
        with self.assertRaises(UnauthenticatedAdminAccessError):
            with self.service._get_session("not-an-admin"):
                pass

    def test_crud_lifecycle(self):
        # Create
        create_data = LlmQuotaRuleCreate(
            tenant_id="tenant-1",
            user_id="user-1",
            provider="openai",
            model="gpt-4o-mini",
            max_rpm=60,
            max_tpm=100000,
            is_active=True,
        )
        rule = self.service.create_rule(create_data, self.admin)
        self.assertIsNotNone(rule.id)
        self.assertEqual(rule.tenant_id, "tenant-1")

        # Get
        fetched = self.service.get_rule(rule.id, self.admin)
        self.assertEqual(fetched.id, rule.id)

        # Update
        update_data = LlmQuotaRuleUpdate(max_rpm=120)
        updated = self.service.update_rule(rule.id, update_data, self.admin)
        self.assertEqual(updated.max_rpm, 120)

        # Update non-existent rule
        self.assertIsNone(self.service.update_rule(9999, update_data, self.admin))

        # List with filters
        rules, count = self.service.list_rules(
            self.admin,
            tenant_id="tenant-1",
            provider="openai",
            model="gpt-4o-mini",
            is_active=True,
        )
        self.assertEqual(count, 1)
        self.assertEqual(rules[0].id, rule.id)

        # List tenants
        tenants = self.service.list_tenants(self.admin)
        self.assertIn("tenant-1", tenants)

        # Delete
        self.assertTrue(self.service.delete_rule(rule.id, self.admin))
        self.assertFalse(self.service.delete_rule(9999, self.admin))
        deleted = self.service.get_rule(rule.id, self.admin)
        self.assertIsNone(deleted)

    def test_preview_effective_limits_calculation(self):
        # Insert two rules: one general, one specific with tighter limits
        self.service.create_rule(
            LlmQuotaRuleCreate(
                tenant_id="tenant-1",
                provider="openai",
                max_rpm=100,
                max_tpm=50000,
                is_active=True,
            ),
            self.admin,
        )
        self.service.create_rule(
            LlmQuotaRuleCreate(
                tenant_id="tenant-1",
                provider="openai",
                model="gpt-4o-mini",
                user_id="user-1",
                max_rpm=50,
                max_tpm=25000,
                is_active=True,
            ),
            self.admin,
        )

        max_rpm, max_tpm = self.service.preview_effective_limits(
            admin=self.admin,
            tenant_id="tenant-1",
            provider="openai",
            model="gpt-4o-mini",
            user="user-1",
        )
        # Should choose the minimum (tighter) limits
        self.assertEqual(max_rpm, 50)
        self.assertEqual(max_tpm, 25000)


if __name__ == "__main__":
    unittest.main()
