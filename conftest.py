import os

# Keep the test suite unrestricted by default: anonymous callers may use any
# configured provider/model. Tests that exercise the allowlist set the env var
# themselves and instantiate Config() directly.
os.environ.setdefault("ANONYMOUS_ALLOWED_PROVIDERS", "*")
os.environ.setdefault("ALLOW_ANONYMOUS", "true")
os.environ.setdefault("ENABLED_SURFACES", "all")
os.environ.setdefault("CACHE_MODE", "disabled")
os.environ.setdefault("INFERENCE_BASE_URL", "")
os.environ.setdefault("EVENTS_ENABLED", "true")

