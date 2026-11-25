"""Shared helpers for the recipe archive tools. Nothing here writes recipe text."""
import hashlib
import html
import json
import re
from pathlib import Path

TOOLS_VERSION = "1.0.0"
USER_AGENT = "Mozilla/5.0 (compatible; personal-recipe-archive)"
TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TOOLS_DIR.parent
DEFAULT_LIBRARY = PROJECT_DIR / "library"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p) -> str:
    return sha256_bytes(Path(p).read_bytes())


def norm(s: str) -> str:
    """The only normalisation allowed: HTML entities decoded, whitespace collapsed."""
    return " ".join(html.unescape(str(s)).split())


def slugify(s: str, maxlen: int = 80) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return (s[:maxlen].strip("-")) or "untitled"


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
