import unittest

from app.core import features, identity, metering, storage


class TestExtensionSeams(unittest.TestCase):
    def test_features_default_oss(self):
        self.assertEqual(features.mode(), "oss")
        self.assertFalse(features.is_enabled("billing"))
        self.assertFalse(features.is_enabled("metering_sink"))

    def test_features_unknown_flag_is_off(self):
        self.assertFalse(features.is_enabled("nonexistent-flag-xyz"))

    def test_billing_router_not_mounted_in_oss(self):
        # The billing seam import path exists and implements the router
        # object, but app.main must not mount it without the registry flip.
        features._FEATURES.clear()
        from app.modules.billing.billing_controller import billing_router

        self.assertIsNotNone(billing_router)  # importable seam target
        # The gate lives in app/main.py; in OSS it stays unmounted.
        self.assertFalse(features.is_enabled("billing"))

    def test_identity_provider_resolves_anonymous(self):
        provider = identity.ApiKeyIdentityProvider()
        self.assertIsNone(provider.resolve({}))
        self.assertIsNone(provider.resolve({"x-api-key": "nonexistent"}))

    def test_identity_dataclass(self):
        ident = identity.Identity(tenant_id="t1", user_id="u1")
        self.assertEqual(ident.tenant_id, "t1")

    def test_metering_noop_sink_is_fail_open(self):
        sink = metering.NoopMeteringSink()
        sink.emit({"provider_cost": 0.0})  # must not raise
        metering.emit_usage({"tenant_id": "t1"}, sink=sink)

    def test_storage_factory_returns_services(self):
        factory = storage.default_factory()
        self.assertIsInstance(factory, storage.StorageFactory)
        # Construction is side-effect-free (no connections opened).
        self.assertIsNotNone(factory.redis)
        self.assertIsNotNone(factory.qdrant)


if __name__ == "__main__":
    unittest.main()
