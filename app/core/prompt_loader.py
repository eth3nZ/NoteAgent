"""Load editable prompt templates from the project-level prompts directory."""

import os
import re
import sys
from pathlib import Path
from typing import Any


DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
PLACEHOLDER_PATTERN = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")


def prompts_dir() -> Path:
    """Return the configured prompt directory."""
    override = os.environ.get("NOTEAGENT_PROMPTS_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if DEFAULT_PROMPTS_DIR.is_dir():
        return DEFAULT_PROMPTS_DIR
    return Path(sys.prefix) / "prompts"


def render_prompt(template_name: str, **values: Any) -> str:
    """Load a Markdown prompt and replace its ``{{name}}`` placeholders."""
    path = prompts_dir() / f"{template_name}.md"
    try:
        template = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"Prompt template not found: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Unable to read prompt template {path}: {exc}") from exc

    required = set(PLACEHOLDER_PATTERN.findall(template))
    missing = sorted(required - values.keys())
    if missing:
        raise ValueError(
            f"Missing prompt value(s) for {template_name}: {', '.join(missing)}"
        )

    rendered = template
    for key in required:
        rendered = rendered.replace(f"{{{{{key}}}}}", str(values[key]))
    return rendered.strip()
