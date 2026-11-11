import os
import tempfile
import unittest
from pathlib import Path

from app.core.prompt_loader import render_prompt


class PromptLoaderTests(unittest.TestCase):
    def test_default_prompt_is_rendered(self):
        prompt = render_prompt(
            "vision_user",
            page_num=3,
            text="alpha beta",
            language="English",
        )
        self.assertIn("page 3", prompt)
        self.assertIn("alpha beta", prompt)
        self.assertNotIn("{{", prompt)

    def test_custom_prompt_directory_and_missing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "sample.md").write_text(
                "Hello {{name}} from {{place}}", encoding="utf-8"
            )
            previous = os.environ.get("NOTEAGENT_PROMPTS_DIR")
            os.environ["NOTEAGENT_PROMPTS_DIR"] = directory
            try:
                self.assertEqual(
                    render_prompt("sample", name="Ada", place="London"),
                    "Hello Ada from London",
                )
                with self.assertRaisesRegex(ValueError, "place"):
                    render_prompt("sample", name="Ada")
            finally:
                if previous is None:
                    os.environ.pop("NOTEAGENT_PROMPTS_DIR", None)
                else:
                    os.environ["NOTEAGENT_PROMPTS_DIR"] = previous


if __name__ == "__main__":
    unittest.main()
