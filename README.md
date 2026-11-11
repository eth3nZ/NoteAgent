# NoteAgent

Turn PDF and PowerPoint course material into detailed study notes:

**PDF / PPTX → Markdown (`.md`) or Jupyter Notebook (`.ipynb`) → optional Notion page**

NoteAgent is a small Python desktop app built with PyQt6. Models run through a cloud API; you do not need to download or run a local model. The default workflow uses a fixed free OpenRouter model, saves progress locally, and pauses when free requests are unavailable.

## 1. Install and launch

You need **Python 3.10 or newer**, an internet connection for generation, and a model-service API key. The commands below are for macOS/Linux; macOS is the current desktop validation target.

```bash
git clone --branch feat/note-review-workflow https://github.com/eth3nZ/NoteAgent.git NoteAgent
cd NoteAgent
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

Already have the project? Open a terminal in its folder and start with the `python3 -m venv` line. For later launches, only run:

```bash
.venv/bin/python main.py
```

On macOS, you can also double-click `setup.command` once, then `run.command` whenever you want to launch. LibreOffice is optional: when installed, it improves PowerPoint rendering.

This development version lives on `feat/note-review-workflow`. The earlier backup branch is preserved separately.

## 2. Connect OpenRouter

Create a key on the [OpenRouter API keys page](https://openrouter.ai/settings/keys), then configure the app:

1. Open **Settings → Models**.
2. Set **Service** to **OpenRouter**.
3. Paste your OpenRouter API key into **OpenRouter key**.
4. Choose a **Free model**. The list loads automatically; use its refresh button if needed.
5. Select **Test connection**, wait for the image-request result, then **Save**.

Leave **Use a paid backup** off for a free-only setup. You can also supply the main key through the `OPENROUTER_API_KEY` environment variable instead of saving it in Settings.

The current default free candidate is `nvidia/nemotron-3.5-super-vl:free`. The app uses the model you select; it does not automatically rotate between different models. The model list filters for image input and advertised strict structured-output support. Gemini and other compatible models can appear in the list. Availability, quotas and regional restrictions depend on the service and hosting provider.

**Test connection consumes a model request.** Refreshing model lists, prices or allowance does not generate notes or upload your document.

## 3. Create your first note

1. Drop a **PDF or PPTX** into the window, or click the file area to browse.
2. Choose **Markdown** or **Jupyter Notebook**, the output language, and a save folder.
3. Leave **Include source pages** off for cleaner notes, or enable it for page references.
4. Leave **Send to Notion** off for your first local export, or configure it as described below.
5. Check the document estimate, then select **Create notes**.

The app sends extracted text and page images to your selected model in groups of up to eight pages. It preserves accepted groups immediately. If a response is truncated or incomplete, it retries with smaller groups; a persistent single-page failure pauses the task.

Each group receives a bounded local outline and previously accepted topic names to keep organization consistent. Export joins adjacent identical topic headings while retaining the original page records. This adds no extra model request or full-document rewrite. Prompt changes invalidate generation caches for new tasks; existing notes and exports remain available.

The language control applies to new generation. Re-exporting saved notes changes their format or source references; it does not translate them.

### Read the estimate

| Card | What it tells you |
| --- | --- |
| **Document** | Total pages, cached pages, and estimated requests still needed. |
| **Generation model** | The selected model and whether the route is free or custom. |
| **Free today** | The account's reported remaining free requests and approximate page coverage. |
| **Paid backup** | **Off**, or a rough cost range for the selected backup. |

For example, **36 pages ≈ 5 requests** means five groups of up to eight pages. **50 free requests** is your account allowance, not the number this document needs. Retries, smaller groups and other account use can change the actual coverage. Missing allowance data appears as **Unknown**.

**Refresh** updates allowance, model prices and cached-page counts together. An estimate reads the document locally; it does not submit its contents to the model.

### Use the right action

| Button or state | What happens |
| --- | --- |
| **Create notes** | Start a task using the selected file, language and model; reuse compatible cached pages. |
| **Working… / Cancel** | Work runs in the background. Cancel stops further scheduling; an in-flight request can take time to exit. |
| **Resume** | Continue the saved task with the free profile, keeping completed pages. |
| **Waiting for free quota** | Keep completed notes and wait until the displayed retry time. |
| **Open notes** | Open the saved file. No generation, new export or Notion write. |
| **Export again** | Save accepted notes in the selected format and folder, without model requests. |
| **Export notes** | Recreate a missing output file from the cached notes. |
| **Export revision** | Save the latest human revision to a new local file. |
| **Export available pages** | Save a partial result with an explicit list of missing pages. |

Selecting a new file, language or generation model returns the main button to **Create notes**. Changing the output format, source-reference option, API key or paid-backup settings does not invalidate accepted content. Reusing cached pages also requires matching document contents, prompts and rendering settings.

## Review and correct your notes

After a task completes:

1. Use **Preview** for rendered headings, tables and code, or **Markdown** for the original text. This uses native Qt; formulas remain in LaTeX in the app and render in Notebook/Notion exports.
2. Select **Review sources** to view an original PDF page or PowerPoint slide alongside its generated note. Rendering runs locally in the background. If the document moved, use **Locate source**; its contents must match the original document.
3. Select **Edit notes**, make changes in Markdown, and choose **Save revision**. Preview is also available inside the editor. Closing an unsaved edit offers Save, Discard or Cancel.
4. Select **Export revision** to save the corrected `.md` or `.ipynb`, or explicitly send it to Notion. Saving a revision itself sends no model or Notion request.

Revisions are stored separately from generated page notes. Re-exporting and publishing use the latest saved revision; cached model results, previous revisions and billing records remain intact. Source-page references describe the generated notes; edited passages are not automatically remapped to source pages. A revision belongs to its saved task and is restored when that task is reopened.

## Optional: publish to Notion

1. Create a Notion integration and copy its token.
2. Connect that integration to your destination page and allow it to insert/update content.
3. Open **Settings → Notion**, enter **Integration token** and **Page URL**, then **Save**. `NOTION_TOKEN` is also supported as an environment variable.
4. Enable **Send to Notion** in the main window.
5. Choose **Create child page** for a separate note, or **Append to page** to add content to the destination.

Generation saves the local file first, then publishes. For completed notes not yet published, **Send to Notion** is a separate action; it does not run the model again. **Open in Notion** opens an existing publication. If a write fails, check the destination before selecting **Retry Notion** because the previous write may already have arrived. Identical confirmed writes are skipped within the saved task.

Notion receives text, native formulas, tables and code blocks. Original page images are not uploaded to Notion, and notebooks are not executed there. You can also import the generated Markdown manually.

If notes were already published, **Send revision** requires confirmation: append adds a revised copy, and create-child makes a new page. It does not overwrite the existing Notion content. Successfully publishing the same revision again is skipped.

## Free quota and optional paid backup

If the free daily quota is exhausted, the app saves completed pages and schedules a retry for **00:05 UTC the following day**. The status shows that time in your local timezone. Retry happens while the app is open or after it is next launched; no background service is installed.

To prepare a paid fallback, enable **Settings → Models → Use a paid backup**, choose a backup model and set your budgets. The main OpenRouter key is reused unless you enter a separate backup key under **Advanced settings**.

- Defaults: **$0.10 per document** and **$1 per calendar month in UTC**.
- Enabling a backup does **not** authorize automatic spending. If the next day's free retry also fails, select **Use paid backup** and confirm that continuation explicitly.
- The estimate uses the selected backup and assumes all remaining pages use it. It is a rough reference, not a quote or spending cap.
- Before each paid request, the app reserves a conservative maximum based on the model context and configured price ceilings. A request can be blocked by its budget even when the rough estimate looks affordable.
- Reported charges replace reservations. An uncertain outcome pauses further requests until the charge is checked against provider activity and reconciled.

Budgets cover requests recorded by this app, including earlier tasks for the same document. They do not cover other apps using your key. After the last uncertain charge for a document is reconciled, its saved pause reason is cleared and the main action returns to **Resume**.

## Custom endpoint: when to use it

Select **Service → Custom endpoint** if you have another service offering an OpenAI-compatible API. It can be a cloud provider or an existing local server; local deployment is not required.

Under **Advanced settings**, enter its API key, **Base URL**, and exact **Model ID**. The custom model field is plain text because model names belong to that service.

For example, with a Base URL of `https://api.example.com/v1`, the app sends generation requests to `https://api.example.com/v1/chat/completions`. Enter the API prefix, not a website address or the complete chat-completions path.

The service must support **image input, streaming chat completions and strict JSON-schema output**. Run **Test connection** before generating. OpenRouter quota checks, zero-price routing and paid-budget enforcement do not apply to custom services; their own billing rules control charges.

For OpenRouter, **Advanced settings** contains optional hosting-provider filters and paid price ceilings. A hosting-provider filter chooses who serves the fixed model; it does not select a different model. Leave these controls alone unless you need a tested route on your network.

## Output files and cache

A normal run saves only the selected `.md` or `.ipynb` file. Re-exported copies receive numbered names such as `Lecture_notes_2.md`; existing exports are preserved. Valid Python blocks become unexecuted notebook cells; incomplete snippets and other languages remain Markdown.

| Location | Contents |
| --- | --- |
| Your output folder; default `~/Desktop/NoteAgent_Output` | Exported notes. |
| `~/.noteagent_config.json` | Settings and any keys saved in the app. |
| `~/.noteagent/tasks.sqlite3` | Checkpoints, accepted notes and request/fee records. |
| `~/.noteagent/workspaces/` | Temporary page-rendering files. |
| `dev_artifacts/` | Development test reports and isolated test outputs. |

Settings are written atomically with user-only file permissions. Keys are not stored in the task database; saved settings are a local JSON file, not an encrypted credential vault. Keep that file private.

The app performs cache cleanup in the background at startup. To check sizes or clean manually, open **Settings → Storage → Clean unused cache**.

- Completed cache unused for 30 days can expire when a saved local export still exists.
- The latest/current task, unfinished tasks, pending Notion retries and uncertain paid requests are retained. A cache with no saved export is kept as a potential only copy.
- Human revisions and their generated source-page records are retained; cache cleanup does not remove them.
- Repeated identical document bodies can be compacted while retaining reconstructable page segments.
- Old, marked rendering workspaces are removed only after their owner process has exited; normal completion/cancellation removes its workspace directly.
- Cleanup keeps source files, exported notes, remote Notion pages and fee records. Regenerating expired content may consume model requests again.

Historical `acceptance-*` and `ui-review` folders are development records, not application cache. They are not automatically deleted or uploaded with the source code.

## Troubleshooting

| Problem | Next step |
| --- | --- |
| No usable models appear | Refresh the model list. If it still fails, check the network and the service's available image/schema-compatible models. |
| Connection rejected or model unavailable | Check the key, exact model ID, hosting filter and regional availability. OpenRouter does not remove upstream regional restrictions. |
| Free allowance is unknown | Save a valid OpenRouter key and select **Refresh**. Custom services do not report OpenRouter allowance. |
| Generation pauses | Read the status and **Activity** details. Keep the task and use **Resume** after resolving the reported problem. |
| Notion publishing fails | Keep the local output; check the token, page access and destination before retrying the write. |
| PowerPoint diagrams look incomplete | Try installing LibreOffice. The fallback renderer has limited support for complex diagrams and fonts. |
| The app cannot start | Confirm Python 3.10+, install `requirements.txt` into `.venv`, then launch with `.venv/bin/python main.py`. |

## Verification and current limitations

The automated suite uses local documents and mock HTTP responses; it does not spend API credits. It covers routing, response validation, truncation, caching, recovery, budgets, source review, persistent revisions, independent export/publishing and UI responsiveness. Code checks include numeric-only Python lines, literal strings, blank lines and alternate Markdown fences.

A recorded live test on **2026-11-08** completed a 38-page PDF with the default free candidate: six inference attempts including a connection probe, about 160 seconds, $0 reported cost, and no new inference on its cached rerun. A separate Notion append/readback check covered that output. These results describe those runs, not guaranteed availability or accuracy for other documents.

On **2026-11-10**, a four-page English sample was tested with the updated prompt. Across an initial run and one follow-up, three free inference requests were made, including one connection probe, with $0 reported cost and no inference during cache reruns. The follow-up retained the worked-example values, produced LaTeX formulas, joined the continuing topic and described the scanned network correctly. It still omitted two visible blank lines inside a multiline string. This is a partial fidelity pass, not a claim of lossless transcription. No paid requests or Notion writes were made.

Known issues and remaining checks:

- **Writing quality:** the English smoke test covers four pages in one group; coherence across a long document and multiple groups still needs live validation. Local heading merging only recognizes identical adjacent topics; it does not infer whether different titles mean the same thing. Structural validation does not establish factual accuracy.
- **Code fidelity:** local formatting preserves numeric code lines, literal strings and blank lines. PDF text extraction can omit whitespace, and the model did not recover visible blank-line spacing from the images in the English sample. Ambiguous editor gutter prefixes are retained rather than guessed away. Review important code against the source before running it yourself.
- **Acceptance coverage:** custom endpoints, real paid requests, use from multiple regions, and a visually complex PPTX still need separate end-to-end checks.
- **PowerPoint fidelity:** without LibreOffice, SmartArt, charts, grouped shapes and unusual fonts may render imperfectly.

The app does not automatically execute code, rewrite the whole document in a second model pass, rotate models, or upload original figures to Notion.

## For development

Run the offline unit suite:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Build fixed English material and run offline fidelity checks:

```bash
.venv/bin/python scripts/quality_baseline.py
```

The versioned PDF includes a definition, a continuing worked example, code with numeric literals and blank lines, and an image-only network diagram. `dev_artifacts/english-quality/review.json` records mechanical checks and a manual checklist for future live runs. Its responses are fixed fixtures: **model quality is not assessed**. The script reads no API keys and makes no network requests.

Run a document acceptance check with mock model responses:

```bash
.venv/bin/python scripts/validate_document.py "/path/to/lecture.pdf" --output-dir dev_artifacts/acceptance-offline
```

This exercises the real local parser, request serialization, checkpoints, both exports and a cached rerun. Fixture text is clearly labeled and does not demonstrate model quality. Acceptance runs default to English; add `--language zh` for Chinese or `--include-source-pages` for references.

<details>
<summary>Optional live acceptance test — consumes free API requests</summary>

Configure an OpenRouter key first. This runner permits only a fixed `:free` model and disables paid and Notion calls.

```bash
.venv/bin/python scripts/validate_document.py "/path/to/lecture.pdf" --live --model nvidia/nemotron-3.5-super-vl:free --output-dir dev_artifacts/acceptance-live
```

The report records page coverage, request attempts, reported usage/cost and export paths without credentials. Keep `dev_artifacts/` out of Git.

</details>

<details>
<summary>Implementation and maintenance</summary>

The app uses PyQt6 Widgets with QThread workers, synchronous `httpx`, PyMuPDF, python-pptx, Pillow, nbformat and standard-library SQLite. There is no separate web frontend, backend service or agent framework in the default flow.

- `app/ui/`: desktop controls, settings, indicators and background workers.
- `app/core/jobs.py`: serial document tasks, resume, export and publishing.
- `app/core/cloud.py`: model requests, validation and service errors.
- `app/core/task_store.py`: checkpoints, cache and the request ledger.
- `prompts/segment_system.md`: the active note-writing prompt. Editing it invalidates generation cache while keeping existing exports.

Legacy page/generator interfaces remain for compatibility. `REVIEW.md` is an older review, not the current setup guide. Branding sources are in `app/assets/`; `scripts/export_icons.py` rebuilds exported icons.

To inspect cache from the command line, without keys or network access:

```bash
.venv/bin/python scripts/clean_cache.py
```

Close the app profile before applying CLI cleanup, or use its Storage tab:

```bash
.venv/bin/python scripts/clean_cache.py --apply
```

</details>
