"""Small, dependency-free helpers used throughout NoteAgent."""

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict


def encode_image_to_base64(image_path: str) -> str:
    """Read an image file and return base64 encoded string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def get_file_extension(filepath: str) -> str:
    """Get the file extension in lowercase."""
    return Path(filepath).suffix.lower()


def validate_file_type(filepath: str) -> bool:
    """Check if the file is PDF or PPTX."""
    ext = get_file_extension(filepath)
    return ext in [".pdf", ".pptx"]


def get_file_size_mb(filepath: str) -> float:
    """Get file size in MB."""
    return os.path.getsize(filepath) / (1024 * 1024)


def ensure_output_dir(directory: str) -> str:
    """Ensure output directory exists, create if not."""
    output_dir = Path(directory).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        raise NotADirectoryError(f"Output path is not a directory: {output_dir}")
    return str(output_dir)


def truncate_text(text: str, max_length: int = 1000) -> str:
    """Truncate text to max_length characters."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def load_config(config_path: str) -> Dict[str, Any]:
    """Load configuration, gracefully recovering from an invalid JSON file."""
    path = Path(config_path).expanduser()
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_config(config: Dict[str, Any], config_path: str) -> None:
    """Atomically save configuration with user-only file permissions."""
    path = Path(config_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(config, file, indent=2, ensure_ascii=False)
            file.write("\n")
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def unique_output_path(output_dir: str, input_path: str, suffix: str) -> str:
    """Return a non-destructive output path based on the input filename."""
    directory = Path(ensure_output_dir(output_dir))
    stem = Path(input_path).stem.strip() or "course"
    candidate = directory / f"{stem}_notes{suffix}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}_notes_{counter}{suffix}"
        counter += 1
    return str(candidate)


def normalize_page_id(value: str) -> str:
    """Extract a Notion page ID from either a raw ID or a Notion page URL."""
    import re

    value = value.strip()
    compact = re.sub(r"[^0-9a-fA-F]", "", value.rsplit("?", 1)[0].rsplit("/", 1)[-1])
    if len(compact) < 32:
        compact = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(compact) < 32:
        raise ValueError("Notion page ID must contain 32 hexadecimal characters")
    compact = compact[-32:].lower()
    return f"{compact[:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:]}"
