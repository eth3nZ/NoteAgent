import unittest

from app.core.agent.evaluator import NoteEvaluator
from app.core.agent.self_improving import SelfImprovingGenerator
from app.core.generator.markdown import MarkdownGenerator, render_source_pages
from app.core.generator.notebook import NotebookGenerator
from app.core.vision.vision_client import PageAnalysis


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
    def test_source_page_visibility_preserves_code_and_missing_page_warning(self):
        content = """# Notes

> Incomplete notes; missing source pages: 3

## Formula
$x=2$

> Source: page 1

```python
text = '''
> Source: page 9
'''
```
~~~text
> 来源：第 10 页
~~~
> 来源：第 2 页
"""
        hidden = render_source_pages(content)
        self.assertNotIn("> Source: page 1\n", hidden)
        self.assertNotIn("> 来源：第 2 页", hidden)
        self.assertIn("> Source: page 9", hidden)
        self.assertIn("> 来源：第 10 页", hidden)
        self.assertIn("missing source pages: 3", hidden)
        self.assertIn("$x=2$", hidden)
        self.assertEqual(render_source_pages(content,True),content)
        self.assertEqual(render_source_pages(hidden),hidden)

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
        generator.MAX_INPUT_TOKENS = 30
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

    def test_markdown_audit_removes_page_and_ide_noise_but_keeps_code(self):
        content = """```markdown
# Lecture

## Page 8
<a id='page-8'></a>

```python
45  import numpy as np
46  value = np.array([1, 2])
```

```text
python.exe C:\\course\\example.py
[1 2]
Process finished with exit code 0
```

## IDE Context
- Python Version: 3.9
- Reader Mode: active
```"""
        result = MarkdownGenerator().audit_document(content, "Lecture")
        self.assertNotIn("<a id=", result.content)
        self.assertNotIn("Page 8", result.content)
        self.assertNotIn("45  ", result.content)
        self.assertNotIn("Process finished", result.content)
        self.assertNotIn("IDE Context", result.content)
        self.assertIn("```python\nimport numpy", result.content)
        self.assertIn("```text\n[1 2]", result.content)
        self.assertEqual(result.issues, [])

    def test_fallback_markdown_has_no_html_toc_or_page_headings(self):
        analyses = [
            PageAnalysis(1, "## Concept\n\nA concise explanation.", "raw"),
            PageAnalysis(
                2,
                "## Example\n\n```python\nprint('ok')\n```",
                "raw",
            ),
        ]
        output = MarkdownGenerator().generate(analyses, "Lecture", "en")
        self.assertNotIn("Table of Contents", output)
        self.assertNotIn("<a id=", output)
        self.assertNotRegex(output, r"(?m)^## Page \d+")
        self.assertIn("> Source: page 2", output)

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

    def test_notebook_preserves_mislabeled_output_and_incomplete_code_as_markdown(self):
        content = """## Training result
```python
--------- Iteration = 23 ---------
b= -2.4 w1= -0.8 , w2= 3.0
```
```python
for value in values:
```
```python
print('actual code')
```
"""
        notebook = NotebookGenerator().generate_from_markdown(content, "Demo")
        code_cells = [cell for cell in notebook.cells if cell.cell_type == "code"]
        self.assertEqual([cell.source for cell in code_cells], ["print('actual code')"])
        self.assertIsNone(code_cells[0].execution_count)
        self.assertEqual(code_cells[0].outputs, [])
        markdown = "\n".join(cell.source for cell in notebook.cells if cell.cell_type == "markdown")
        self.assertIn("```python\n--------- Iteration = 23", markdown)
        self.assertIn("```python\nfor value in values:", markdown)


if __name__ == "__main__":
    unittest.main()
