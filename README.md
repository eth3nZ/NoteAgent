# NoteAgent

NoteAgent turns PDF and PowerPoint course material into a Markdown file, a Jupyter notebook, and optionally a Notion page. It renders every page, combines visual understanding with extracted text, rewrites the result into study notes, and keeps a local copy even if Notion publishing fails.

## Quick start on macOS

1. Double-click `setup.command` once. It creates an isolated Python environment and installs the app's dependencies.
2. Double-click `run.command`.
3. Open **Connections** and choose one of the free model options below.
4. Drop in a `.pdf` or `.pptx`, choose the local format, and select **Generate notes**.

You can also start it from Terminal:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

Python 3.10 or newer is required. Python 3.11 or 3.12 is recommended. The setup launcher automatically skips an older macOS system Python when a newer Homebrew or Miniconda Python is available.

## Free model options

### Hosted and easiest: OpenRouter

The default model is `qwen/qwen3.8-27b:free`, a free multimodal Qwen model that accepts both slide images and text. `openrouter/free` is also supported if you prefer automatic routing among currently available free models.

1. Create an account at [OpenRouter](https://openrouter.ai/).
2. Create a key at [OpenRouter API Keys](https://openrouter.ai/settings/keys). A card is not required to start.
3. In NoteAgent, open **Connections**, choose **OpenRouter · free hosted**, and paste the key.

OpenRouter's no-credit free plan is currently limited to 50 requests per day. A typical 20-slide deck uses roughly 21–23 requests because slides are analyzed individually and the results are rewritten once. Free model availability can change, so `openrouter/free` is a useful fallback.

### Fully local and no API key: Ollama

Local inference has no provider quota and keeps the source document on your Mac. It needs enough memory and is slower than a hosted endpoint.

1. Install [Ollama](https://ollama.com/).
2. Pull a vision model:

   ```bash
   ollama pull qwen3-vl:4b
   ```

   Use `qwen3-vl:8b` instead for better output on a Mac with more memory.

3. Keep Ollama running.
4. In NoteAgent, open **Connections** and choose **Ollama · free local**. No API key is needed.

## Notion publishing

NoteAgent supports two modes:

- **Create a new note inside the configured page** creates a child page containing the generated Markdown.
- **Append to the configured page** adds the generated notes to the end of an existing page.

Setup:

1. Follow Notion's [connection instructions](https://www.notion.com/help/create-integrations-with-the-notion-api) and create an internal connection or personal access token.
2. Give it **Insert Content** and **Update Content** capabilities.
3. In Notion, add the connection to the parent/target page.
4. Paste the token and the page URL into NoteAgent's **Connections → Notion** tab.
5. Enable **Also publish to Notion** in the main window.

Credentials are stored in `~/.noteagent_config.json` with user-only (`0600`) permissions. You can avoid storing tokens by leaving them blank and starting NoteAgent with `OPENROUTER_API_KEY` and/or `NOTION_TOKEN` environment variables.

## PowerPoint rendering

For accurate slide images, NoteAgent uses LibreOffice when `soffice` is available. Without LibreOffice it falls back to an internal renderer that preserves picture and text-box positions, simple shapes, and tables. The fallback is much better than a text-only screenshot but cannot reproduce every PowerPoint animation, SmartArt feature, embedded video, or unusual font.

To point NoteAgent at a non-standard LibreOffice installation, set `NOTEAGENT_SOFFICE_PATH` before starting the app.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The test suite does not call OpenRouter, Ollama, or Notion.
