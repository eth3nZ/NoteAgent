import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from app.utils.helpers import load_config, normalize_page_id, save_config, unique_output_path


class HelperTests(unittest.TestCase):
    def test_config_round_trip_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config({"token": "secret", "语言": "中文"}, str(path))
            self.assertEqual(load_config(str(path))["token"], "secret")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_invalid_config_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(load_config(str(path)), {})

    def test_notion_url_is_normalized(self):
        value = normalize_page_id(
            "https://www.notion.so/My-Notes-0123456789abcdef0123456789abcdef?pvs=4"
        )
        self.assertEqual(value, "01234567-89ab-cdef-0123-456789abcdef")

    def test_unique_output_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            first = unique_output_path(directory, "/tmp/My Deck.pptx", ".md")
            Path(first).touch()
            second = unique_output_path(directory, "/tmp/My Deck.pptx", ".md")
            self.assertTrue(second.endswith("My Deck_notes_2.md"))


if __name__ == "__main__":
    unittest.main()
