# Desktop and quality acceptance — 2026-11-26

The React/WKWebView interface uses the existing Python pipeline and is now the default macOS entry point. The Qt development entry remains available. No paid requests or Notion writes were used for this acceptance.

## Real free-model quality

The fixed benchmark contains 12 English pages: formulas, a worked calculation, code with visible blank lines, a scanned network, continuing topics, comparisons, incidental training logs and non-content pages. Both services use the same images, extracted text, prompt and structured response contract. This is authored test material, not a statistical benchmark.

| Run | Coverage | Generation attempts | Elapsed | Reported input / output tokens | Reported cost |
| --- | --- | --- | --- | --- | --- |
| OpenRouter Nemotron free, fixed sample | 12/12 | 2 | 15.832 s | 25,254 / 1,116 | $0 |
| Targeted follow-up, same model | 4/4 | 1 | 5.282 s | 8,710 / 461 | $0 |
| Existing 38-page neural-network PDF | 38/38 | 7, including two network failures | 99.129 s | 112,024 / 5,718 from successful responses | $0 |
| Google direct Gemini | Pending | 0 | — | — | — |

The small-sample cap was six total attempts; three were used. The full-document cap was seven; all seven were used. There were no connection probes, further generation attempts, paid requests or Notion writes in these runs. Failed free network requests supplied no usage response. The full run is a baseline continuation, not a comparison winner: no confirmed unbilled Google project was available.

Manual findings:

- The sample retained gradient values 1.6, 1.28 and 2.56 and the scanned 2–2–1 topology. Cover and thank-you pages produced internal non-content receipts, omitted from final notes.
- A definition page borrowed a worked example from the following page, which then repeated it. This weakens page attribution and concision.
- Two visible blank lines inside a triple-quoted Python string were lost in both the sample and follow-up. The model's claim that they were preserved was incorrect. Numeric and string tokens passing checks do not establish exact transcription.
- The follow-up called `exp(log_var)` standard deviation despite defining `log_var` as log variance, and failed to flag the source inconsistency. The code remained unchanged. This is a substantive explanation error, not a formatting difference.
- Incidental timing columns, generated layer names and an optimizer setting labeled as a course requirement remained. Instructions improved the intended style but are not reliably obeyed by this model.
- In the 38-page run, selected checks covered pages 1, 4, 9, 15, 20, 26, 32 and 38. The line example's values −15 and 21 were correct. The code-image training loop retained its learning rate, signs and update operation; whitespace was not exact. The source screenshot showed 15/15 correct at iteration 17, matching the note. Three hidden layers and the learning-rate explanation matched the checked source.

Source images for pages 15 and 20 were rendered with Poppler and inspected. This was a spot check, not an exhaustive proof of every formula or omission. Literal matching can reject equivalent mathematical notation; invisible trailing spaces cannot be judged from a PDF image. No model self-score was used. Preserve human source review for important code and derivations.

## Interface and safety

- Python: 229 offline tests, including both adapters, confirmation revocation, credential isolation, paid-primary reservations/settlement, cooperative cancel/close, slow credential access and bridge whitelisting. Tests use injected credentials and mock HTTP, not the real Keychain or accounts.
- Frontend: six unit tests and six browser interaction tests. Coverage includes safe math/code/table rendering, dynamic-code-free native bootstrap, duplicate starts, cancellation/resume, stale source responses, revisions, selected-cache removal and explicit Notion retry confirmation.
- Actual WKWebView app: restored the long note, rendered all 34 second-level sections, jumped to its final section, displayed source pages 1 and 15, opened model/storage settings and quit normally. No model call was needed. Manual interactions remained responsive; this is not an instrumented frame-rate guarantee.
- Real output exposed a closing `$$` on a formula line that hid later sections in the reader. A view-only normalization and regression test now keep those sections readable. Saved Markdown and code are untouched.
- The page keeps a strict CSP, sanitized HTML, local formula fonts, suppressed remote images and explicit HTTP(S) link opening. Only `call` and `get_state` are exposed; saved keys are never returned. The pywebview 6.2 bridge has a scoped static bootstrap to avoid its default eval/Function wrappers.
- Both generated bundles pass local code-signature verification. Every Mach-O dependency is signed individually, including nested Pillow libraries. These are development ad-hoc signatures, not Apple notarization.

## Complete app measurements

macOS arm64; Homebrew Python 3.12.12; matching core dependency versions; pywebview 6.2.1. The Qt comparison uses the same interpreter/core architecture with the legacy interface. Values count regular file bytes, excluding symlink duplicates; they are not installer download sizes.

| Bundle | Complete app size | Spawn-to-diagnostic samples | Median |
| --- | --- | --- | --- |
| React + system WKWebView | 133.90 MiB | 2.523 s, 1.599 s, 1.377 s | 1.599 s |
| PyQt baseline | 358.17 MiB | 3.467 s, 2.254 s, 0.926 s | 2.254 s |

All launches used disposable empty profiles without credentials. Native readiness is the first successful frontend state snapshot (backend ready); Qt readiness is the first GUI event after showing the window. They are practical startup indicators with different boundaries. Three samples and warm caches do not support a general speed claim. The long-note native launch also returned its first state after about three seconds.

The native bundle excludes PyQt, Node and Chromium. Both sizes include Python, document libraries and repository resources; the Qt comparison retains those resources too. System WKWebView itself is not copied into either app. Raw observations, source renders and generated notes remain in ignored `dev_artifacts/react-migration-20261126/` rather than the repository.

## Still pending

Direct Gemini quality awaits a configured, explicitly confirmed unbilled project. Real paid billing, custom-endpoint acceptance, multiple-region availability and a complex PPTX need separate checks. Current notes are useful drafts requiring review; this work does not establish lossless code transcription or factual accuracy. The default free model stays unchanged.
