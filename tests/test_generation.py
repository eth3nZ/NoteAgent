import unittest

from app.core.agent.evaluator import NoteEvaluator
from app.core.agent.self_improving import SelfImprovingGenerator
from app.core.generator.notebook import NotebookGenerator


class FakeEvaluator:
    def evaluate(self, note, raw, language):
        return 100, []


class FakeTextClient:
    def __init__(self):
        self.prompts = []

    def complete_text(self, messages, stream_callback=None):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        result = "## Notes\n\n- " + prompt[-30:]
        if stream_callback:
            stream_callback(result)
        return result


class GenerationTests(unittest.TestCase):
    def test_code_free_notes_can_reach_quality_threshold(self):
        raw = "Neural networks use layers. Gradient descent updates weights."
        note = """# Neural Networks

## Layers

- Neural networks use layers.

## Training

- Gradient descent updates weights.
"""
        score, issues = NoteEvaluator().evaluate(note, raw, "en")
        self.assertGreaterEqual(score, 82)
        self.assertNotIn("Code", " ".join(issues))

    def test_long_input_is_chunked_without_truncation(self):
        client = FakeTextClient()
        generator = SelfImprovingGenerator(client, evaluator=FakeEvaluator())
        generator.MAX_INPUT_CHARS = 80
        content = "\n\n".join(
            f"<!-- NOTEAGENT_PAGE:{index} -->\npage-{index}-" + ("x" * 35)
            for index in range(1, 7)
        )
        result = generator.generate(content, "en")
        self.assertGreater(len(client.prompts), 1)
        submitted = "\n".join(client.prompts)
        for index in range(1, 7):
            self.assertIn(f"page-{index}", submitted)
        self.assertTrue(result.content)

    def test_notebook_preserves_python_code_order(self):
        content = """## Intro

Before.

```python
print('hello')
```

After.

```javascript
console.log('kept as markdown');
```
"""
        notebook = NotebookGenerator().generate_from_markdown(content, "Demo")
        self.assertEqual([cell.cell_type for cell in notebook.cells], ["markdown", "markdown", "code", "markdown"])
        self.assertIn("Before", notebook.cells[1].source)
        self.assertEqual(notebook.cells[2].source, "print('hello')")
        self.assertIn("After", notebook.cells[3].source)
        self.assertIn("```javascript", notebook.cells[3].source)


if __name__ == "__main__":
    unittest.main()
