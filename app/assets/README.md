# NoteAgent logo

A connected **NA** monogram in blue violet and coral on a light butter-yellow background. Folded corners and the small note inside the A refer to the app's document-to-notes workflow.

- `noteagent.png`: original 1024 × 1024 artwork, with an opaque background.
- `noteagent-rounded.png`: application and window icon, with a smooth rounded tile and transparent outside padding.
- `noteagent.icns`: rounded icon in multiple sizes for a future macOS application bundle.
- `noteagent.ico`: rounded icon in multiple sizes for Windows packaging.
- `noteagent-menubar.svg`: simplified black NA silhouette on transparency. Qt renders normal/Retina images at a fixed 24 × 16 points and marks them as native macOS templates, so the OS chooses black or white without expanding the SVG to fill a taller menu bar.

Original artwork created with the built-in imagegen tool. The rounded boundary is rendered precisely with Qt, preserving the original artwork without stray edge pixels. Rebuild it and the standard icon containers with `.venv/bin/python scripts/export_icons.py`. The monochrome menu symbol uses editable vector paths for clarity at small sizes. No new runtime dependency was added. Qt's application icon is set at startup. The standalone ICNS/ICO files are packaging assets; this does not change the icon of the existing shell launcher in Finder.

## Original artwork generation prompt

> Use case: logo-brand
> Asset type: finished app icon for NoteAgent.
> Input images: the provided previous icon is an edit target; redesign its identity rather than preserving the green palette or the single N.
> Primary request: Redesign this as a brighter, more creative, minimal NA monogram. The letters must read clearly as two uppercase letters, N then A, not M, not just N. Make the N and A feel like two folded note ribbons that fit together into one clever compact mark. A small folded-paper tip and an A counter shaped like a folded page introduce one quiet visual idea. Maintain very simple thick geometry and readable negative space. No extra objects.
> Color palette: a cheerful pale butter-yellow background (#FFF0C2), a rich muted periwinkle/ink-blue N (#4B58A5), and a warm coral A (#DE725D). Cream is allowed only on a tiny folded edge. No green. No black areas anywhere.
> Style: crisp flat vector-like identity, beautifully balanced, friendly and professional, no gradients, no lighting, no textures, no mottling or smoky patches. Smooth slightly rounded geometric corners, not inflated 3D letters.
> Composition: one square final image with an opaque uniform pale butter-yellow background reaching every edge. The centered large NA mark occupies about 65 percent of the canvas width, with roomy equal margins. Do NOT render a background around a tile, transparent corners, a black preview mat, or a scene. This is the actual icon art, not a mockup. It must remain legible at 32 pixels.
> Text (verbatim): "NA". This is the only lettering. No wordmark, no slogan.
> Avoid: dark green tile, black, gradients, shadows, floating sparkles, robot faces, circuitry, decorative dots, tiny writing, book illustrations beside the letters, watermarks, presentation sheets. Produce a single polished original icon.
