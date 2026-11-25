"""Fixed template: recipe.jsonld (or microdata fields) -> recipe.md.

Only whitespace and HTML entities are normalised. Missing fields are omitted, never filled.
Every line is "<fixed prefix><source text>" so verify.py can check each one against the original.
"""
import json
import re

from common import norm

NOTE_PREFIX = "> "
SEPARATOR = "---"  # between the recipes of one page; structural, never checked against the page
LABELS = ["Author", "Yield", "Prep time", "Cook time", "Total time", "Category", "Cuisine", "Keywords", "Description"]


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _text_items(v):
    out = []
    for x in _as_list(v):
        if isinstance(x, (str, int, float)) and not isinstance(x, bool):
            n = norm(x)
            if n:
                out.append(n)
        elif isinstance(x, dict) and isinstance(x.get("name"), str) and norm(x["name"]):
            out.append(norm(x["name"]))
    return out


def _author(v):
    out = []
    for x in _as_list(v):
        if isinstance(x, str) and norm(x):
            out.append(norm(x))
        elif isinstance(x, dict) and isinstance(x.get("name"), str) and norm(x["name"]):
            out.append(norm(x["name"]))
    return out


def _steps(v, heading=None, out=None):
    """Flatten recipeInstructions into [(heading|None, [step, ...]), ...]."""
    if out is None:
        out = []

    def cur(h):
        if not out or out[-1][0] != h:
            out.append((h, []))
        return out[-1][1]

    for x in _as_list(v):
        if isinstance(x, str):
            n = norm(x)
            if n:
                cur(heading).append(n)
        elif isinstance(x, list):
            _steps(x, heading, out)
        elif isinstance(x, dict):
            if "itemListElement" in x:
                h = norm(x["name"]) if isinstance(x.get("name"), str) and norm(x["name"]) else heading
                _steps(x["itemListElement"], h, out)
            else:
                t = x.get("text") if isinstance(x.get("text"), str) and norm(x["text"]) else x.get("name")
                if isinstance(t, str) and norm(t):
                    cur(heading).append(norm(t))
    return out


def _notes(notes):
    return [norm(n) for n in (notes or []) if norm(n)]


def model_from_jsonld(obj: dict, notes=None) -> dict:
    m = {"name": norm(obj["name"]) if isinstance(obj.get("name"), str) else None}
    m["Author"] = _author(obj.get("author"))
    m["Yield"] = _text_items(obj.get("recipeYield"))
    m["Prep time"] = _text_items(obj.get("prepTime"))
    m["Cook time"] = _text_items(obj.get("cookTime"))
    m["Total time"] = _text_items(obj.get("totalTime"))
    m["Category"] = _text_items(obj.get("recipeCategory"))
    m["Cuisine"] = _text_items(obj.get("recipeCuisine"))
    kw = obj.get("keywords")
    m["Keywords"] = _text_items(kw)
    m["Description"] = _text_items(obj.get("description"))
    m["ingredients"] = _text_items(obj.get("recipeIngredient") if "recipeIngredient" in obj else obj.get("ingredients"))
    m["sections"] = [s for s in _steps(obj.get("recipeInstructions")) if s[1]]
    m["notes"] = _notes(notes)
    return m


def model_from_fields(f: dict, notes=None) -> dict:
    m = {"name": norm(f["title"]) if f.get("title") else None}
    m["Author"] = _text_items(f.get("author"))
    m["Yield"] = _text_items(f.get("yields"))
    m["Prep time"] = m["Cook time"] = m["Total time"] = []
    m["Category"] = _text_items(f.get("category"))
    m["Cuisine"] = _text_items(f.get("cuisine"))
    m["Keywords"] = []
    m["Description"] = []
    m["ingredients"] = _text_items(f.get("ingredients"))
    m["sections"] = [(None, _text_items(f.get("instructions")))]
    m["notes"] = _notes(notes)
    return m


def model_from_card(c: dict) -> dict:
    """c: the dict stored as recipe.card.json (title, yield, total_time, ingredients, directions, notes)."""
    m = {"name": norm(c["title"]) if c.get("title") else None}
    for lab in LABELS:
        m[lab] = []
    m["Yield"] = _text_items(c.get("yield"))
    m["Total time"] = _text_items(c.get("total_time"))
    m["ingredients"] = _text_items(c.get("ingredients"))
    m["sections"] = [(None, _text_items(c.get("directions")))] if c.get("directions") else []
    m["notes"] = _notes(c.get("notes"))
    return m


def render_model(m: dict) -> str:
    lines = []
    if m["name"]:
        lines += [f"# {m['name']}", ""]
    head = [f"{lab}: {v}" for lab in LABELS for v in m[lab]]
    if head:
        lines += head + [""]
    if m["ingredients"]:
        lines += ["## Ingredients", ""] + [f"- {x}" for x in m["ingredients"]] + [""]
    if m["sections"]:
        lines += ["## Instructions", ""]
        for h, steps in m["sections"]:
            if h:
                lines += [f"### {h}", ""]
            lines += [f"{i}. {s}" for i, s in enumerate(steps, 1)] + [""]
    if m.get("notes"):
        lines += ["## Notes", ""] + [f"{NOTE_PREFIX}{x}" for x in m["notes"]] + [""]
    return "\n".join(lines).rstrip("\n") + "\n"


def render_recipes(models) -> str:
    """All recipes of one page in page order, joined by the fixed separator line."""
    return ("\n" + SEPARATOR + "\n\n").join(render_model(m) for m in models)


def render_jsonld_list(bs, notes=None) -> str:
    """bs: the byte slices of every Recipe object, in page order. Cook's notes go after the last recipe."""
    ms = [model_from_jsonld(json.loads(b.decode("utf-8"))) for b in bs]
    ms[-1] = model_from_jsonld(json.loads(bs[-1].decode("utf-8")), notes)
    return render_recipes(ms)


def render_jsonld_bytes(b: bytes, notes=None) -> str:
    return render_jsonld_list([b], notes)


def render_fields(f: dict, notes=None) -> str:
    return render_model(model_from_fields(f, notes))


def render_card(c: dict) -> str:
    """c: recipe.card.json; further cards (key "more_cards") follow the first, each with its own notes."""
    first = {k: v for k, v in c.items() if k != "more_cards"}
    return render_recipes([model_from_card(first)] + [model_from_card(x) for x in c.get("more_cards") or []])


LINE_RE = [
    ("title", re.compile(r"^# (.+)$")),
    ("label", re.compile(r"^(%s): (.+)$" % "|".join(LABELS))),
    ("ingredient", re.compile(r"^- (.+)$")),
    ("step", re.compile(r"^\d+\. (.+)$")),
    ("heading", re.compile(r"^### (.+)$")),
    ("note", re.compile(r"^> (.+)$")),
]


def classify_line(line: str):
    """Return (kind, text) for a recipe.md line, or None if blank/structural, or ('bad', line)."""
    if line == "" or line in ("## Ingredients", "## Instructions", "## Notes", SEPARATOR):
        return None
    for kind, rx in LINE_RE:
        m = rx.match(line)
        if m:
            return kind, m.group(m.lastindex)
    return "bad", line
