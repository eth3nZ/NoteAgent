# NoteAgent review

Reviewed and repaired in the isolated sibling folder on October 9, 2026. The original `NoteAgent` folder was not modified.

## What was broken

- The active Python environment could not import the UI (`PyQt6`) or notebook writer (`nbformat`), and the project had no setup/launch workflow.
- PowerPoint pages were not actually rendered. Text was drawn from the top-left onto a blank image, while pictures, diagrams, tables, shape positions, and most layout information were lost before vision analysis.
- The pipeline paid for page-level vision analysis and then discarded every result. Final notes were produced only from extracted text.
- Source material was silently truncated to 8,000 characters, so longer decks lost most of their content.
- Every notebook was represented as one synthetic “page 0”; code blocks were also moved out of their original order.
- Output always used `course_notes.*` and silently overwrote an earlier run.
- The configured free OpenRouter model IDs were stale.
- The local quality scorer made a code-free note unable to reach the quality threshold and handled Chinese coverage poorly, causing unnecessary rewrite calls.
- Model streaming was wired into the worker but never shown by the UI.
- Closing during a blocking request could freeze while waiting indefinitely for the worker.
- A malformed settings JSON file could prevent the UI from opening, and credentials were saved without enforcing user-only file permissions.
- There were no tests, no README, and no Notion output path.

## What is fixed

- Accurate PowerPoint rendering through LibreOffice when available, plus a layout-aware fallback for text boxes, pictures, simple shapes, and tables.
- Page-level vision results now feed the rewrite stage. Long material is split at page boundaries instead of truncated.
- Markdown and notebook exports preserve content order; Python fences become executable notebook cells while other languages remain correctly fenced Markdown.
- Source-based, non-destructive output names such as `Lecture_notes.md`, followed by `_2`, `_3`, and so on.
- Optional Notion publishing can create a child note or append to an existing page using Notion's Markdown API.
- Hosted free Qwen and local Ollama presets, clear provider errors, one bounded retry, and a text-only fallback when a free rate limit is exhausted.
- A responsive UI with live output, progress, cancellation, connection settings, local/Notion destinations, and direct open buttons.
- Atomic settings writes with `0600` permissions and safe recovery from invalid JSON.
- Double-click macOS setup and run launchers.
- Fifteen automated tests covering parsing, generation, outputs, HTTP payloads, Notion, and UI construction.

## Current verification boundary

All local tests pass and LibreOffice rendering was exercised with a generated PowerPoint. The authenticated OpenRouter and Notion paths were verified against their current request contracts with local mock endpoints, but not called with a real user key. A final acceptance test should use one representative real PDF, one visually complex real deck, an OpenRouter or Ollama connection, and the intended Notion target page.

## Remaining product limitations

- Free hosted inference is quota-limited; a large deck can exhaust the daily request allowance. Local Ollama avoids this.
- Without LibreOffice, SmartArt, animations, video, uncommon fonts, and some grouped PowerPoint objects cannot be perfectly reproduced by the fallback renderer.
- Notion receives a native page built from Markdown, not an embedded `.ipynb` runtime. Code is preserved as code blocks.
- Credentials are protected by local file permissions but are not stored in macOS Keychain. Environment variables are supported when stronger secret handling is preferred.
