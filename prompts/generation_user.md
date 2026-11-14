Turn the page-by-page analysis below into cohesive, reviewable Markdown notes written in {{language}}.

Material:

---
{{raw_content}}
---

Requirements:
1. Merge repetition while preserving definitions, procedures, formulas, examples, code, and diagram meaning.
2. Organize by topic with one document title and a consistent `##` / `###` heading hierarchy. Do not create a table of contents, page-by-page sections, `Page N` headings, or HTML anchors.
3. Preserve important details recovered visually even when they are absent from the extracted text.
4. Preserve source code exactly in correctly labeled fenced code blocks. Remove editor line numbers and IDE or terminal chrome, but never add imports, comments, variables, or executable lines that were not present in the source.
5. Keep only meaningful program output in a `text` fence or Markdown table. Remove commands, executable paths, status bars, and successful-exit messages.
6. Summarize diagrams in 1–3 sentences. Keep their `(source page N)` reference so a person can review the original.
7. Use LaTeX for formulas. Return only the notes and never wrap the entire response in a `markdown` code fence.
8. Entries marked <!-- noteagent:non-content --> are processed pages with no study content. Omit
   them from the visible notes. Remove cover/agenda/demo-title narration, decorative descriptions,
   repeated introductions and incidental timing logs; retain actionable course requirements briefly.
9. Define a concept once in its section. Add new facts, qualifications or examples from later pages
   under that concept. Use short paragraphs, parallel bullets and compact comparison tables as useful.
10. State chart findings and useful model architecture, rather than inventorying visual layout or
    generated layer names. Explain code purpose and non-obvious decisions without a line-by-line retelling.
11. Preserve source inconsistencies as a short Source check near the affected code or formula.
    Do not silently fix source code or invent an explanation that makes contradictory material agree.
