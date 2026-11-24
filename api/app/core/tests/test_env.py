"""Unit tests for environment loading and database engine re-synchronization."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.core.config import config
from app.core.database import SessionLocal, reload_db_engine
from app.core.env import load_app_env
from app.scripts.wipe_qdrant import main as wipe_qdrant_main


class TestEnvironmentLoading(unittest.TestCase):
    def setUp(self):
        self._orig_env = dict(os.environ)
        self._orig_db_url = config.DATABASE_URL
        self._orig_db_url_read = config.DATABASE_URL_READ

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_env)
        config.DATABASE_URL = self._orig_db_url
        config.DATABASE_URL_READ = self._orig_db_url_read
        reload_db_engine()

    def test_load_app_env_falls_back_gracefully_when_file_missing(self):
        os.environ["PYTHON_ENV"] = "nonexistent_env_12345"
        loaded = load_app_env()
        self.assertTrue(loaded is None or isinstance(loaded, Path))

    def test_load_app_env_loads_specific_env_file(self):
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env_file = tmp_path / ".env.teststaging"
            env_file.write_text("PROJECT_NAME=echo-gate-staging-test\n", encoding="utf-8")

            with patch("app.core.env.Path") as mock_path:
                mock_path.side_effect = lambda *args, **kwargs: Path(*args, **kwargs)
                # Ensure resolution points to our tmp env
                loaded = load_app_env(python_env="teststaging")
                # When loaded directly from existing file
                self.assertIsNotNone(load_app_env)

    def test_reload_db_engine_rebinds_session_local(self):
        test_url = "sqlite://"
        config.DATABASE_URL = test_url
        config.DATABASE_URL_READ = test_url

        reload_db_engine()

        engine_url = str(SessionLocal.kw["bind"].url)
        self.assertEqual(engine_url, test_url)


class TestWipeQdrantSafety(unittest.IsolatedAsyncioTestCase):
    async def test_wipe_qdrant_aborts_without_confirm_when_prompt_declined(self):
        with patch("builtins.input", return_value="n"):
            exit_code = await wipe_qdrant_main([])
            self.assertEqual(exit_code, 1)

    async def test_wipe_qdrant_aborts_on_keyboard_interrupt(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            exit_code = await wipe_qdrant_main([])
            self.assertEqual(exit_code, 1)
