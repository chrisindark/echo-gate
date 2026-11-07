import tempfile
import unittest
from pathlib import Path

from scripts.changeset import (
    Changeset,
    calculate_next_version,
    create_changeset,
    parse_changeset_file,
)


class TestChangeset(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_valid_changeset(self):
        file_path = self.dir_path / "20261107-test-change.md"
        file_path.write_text(
            "---\n"
            "type: minor\n"
            "area: gateway\n"
            "breaking: false\n"
            "---\n\n"
            "Natural streaming playback for cache hits.\n",
            encoding="utf-8",
        )

        cs = parse_changeset_file(file_path)
        self.assertIsNotNone(cs)
        self.assertEqual(cs.bump_type, "minor")
        self.assertEqual(cs.area, "gateway")
        self.assertFalse(cs.breaking)
        self.assertEqual(cs.summary, "Natural streaming playback for cache hits.")

    def test_parse_invalid_changeset_format(self):
        file_path = self.dir_path / "bad.md"
        file_path.write_text("No frontmatter here", encoding="utf-8")
        with self.assertRaises(ValueError):
            parse_changeset_file(file_path)

    def test_parse_invalid_bump_type(self):
        file_path = self.dir_path / "bad-type.md"
        file_path.write_text(
            "---\ntype: colossal\narea: gateway\n---\nSome text",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            parse_changeset_file(file_path)

    def test_parse_empty_summary(self):
        file_path = self.dir_path / "empty-body.md"
        file_path.write_text(
            "---\ntype: patch\narea: gateway\n---\n   \n",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            parse_changeset_file(file_path)

    def test_parse_ignores_readme_and_non_md(self):
        readme = self.dir_path / "README.md"
        readme.write_text("# Documentation", encoding="utf-8")
        self.assertIsNone(parse_changeset_file(readme))

        txt = self.dir_path / "notes.txt"
        txt.write_text("Some notes", encoding="utf-8")
        self.assertIsNone(parse_changeset_file(txt))

    def test_calculate_next_version(self):
        current = "0.1.0"
        # No changesets -> current
        self.assertEqual(calculate_next_version(current, []), "0.1.0")

        # Patch
        cs_patch = Changeset(
            path=Path("dummy"),
            bump_type="patch",
            area="gateway",
            breaking=False,
            summary="Fix bug",
        )
        self.assertEqual(calculate_next_version(current, [cs_patch]), "0.1.1")

        # Minor
        cs_minor = Changeset(
            path=Path("dummy"),
            bump_type="minor",
            area="gateway",
            breaking=False,
            summary="Add feature",
        )
        self.assertEqual(calculate_next_version(current, [cs_patch, cs_minor]), "0.2.0")

        # Major in 0.x -> 0.2.0
        cs_major = Changeset(
            path=Path("dummy"),
            bump_type="major",
            area="gateway",
            breaking=True,
            summary="Breaking API rewrite",
        )
        self.assertEqual(calculate_next_version("0.1.0", [cs_major]), "0.2.0")

        # Major in 1.x -> 2.0.0
        self.assertEqual(calculate_next_version("1.2.3", [cs_major]), "2.0.0")

    def test_create_changeset_file(self):
        import scripts.changeset as cs_module

        orig_dir = cs_module.CHANGESETS_DIR
        try:
            cs_module.CHANGESETS_DIR = self.dir_path
            path = create_changeset(
                bump_type="patch",
                area="web",
                summary="Fix chat playground layout glitch",
                breaking=False,
            )
            self.assertTrue(path.exists())
            parsed = parse_changeset_file(path)
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed.bump_type, "patch")
            self.assertEqual(parsed.area, "web")
            self.assertEqual(parsed.summary, "Fix chat playground layout glitch")
        finally:
            cs_module.CHANGESETS_DIR = orig_dir


if __name__ == "__main__":
    unittest.main()
