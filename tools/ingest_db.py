#!/usr/bin/env python
"""Ingest all recipes from library/ into the normalized Postgres database.

Reads DATABASE_URL_UNPOOLED or DATABASE_URL from .env.local (or environment).
Idempotent: safe to re-run without overwriting user ratings, effort, tags, feedback, or brand preferences.
"""
import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sys

from dotenv import load_dotenv
import isodate
import psycopg
from psycopg.types.json import Jsonb
import yaml

TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))

import render as rd
from common import norm
from ingredient_norm import is_staple_name, parse_ingredient

SCHEMA_PATH = PROJECT_DIR / "db" / "schema.sql"
SOURCES_PATH = PROJECT_DIR / "sources.yaml"
DEFAULT_LIBRARY = PROJECT_DIR / "library"


@dataclass
class RecipeRecord:
    source_key: str
    kind: str
    structured: bool
    title: str
    author: str | None = None
    source_site: str | None = None
    source_url: str | None = None
    yield_text: str | None = None
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    total_minutes: int | None = None
    cuisine: str | None = None
    category: str | None = None
    description: str | None = None
    instructions: list[dict] = field(default_factory=list)
    source_notes: list[str] = field(default_factory=list)
    raw_text: str = ""
    ingredients: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    ai_allowed: bool = True


def load_ai_policy() -> dict[str, bool]:
    """Return {source_id: ai_allowed} from sources.yaml. Defaults to True unless restricted."""
    policy = {}
    if not SOURCES_PATH.is_file():
        return policy
    data = yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8")) or {}
    for src in data.get("sources") or []:
        sid = src.get("id")
        if not sid:
            continue
        note = " ".join(str(src.get(k) or "") for k in ("collect_note", "limits")).lower()
        disallowed = "not to be read" in note or "no recipe text passes through an ai" in note
        policy[sid] = not disallowed
    return policy


def parse_minutes(val) -> int | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(round(val)) if val > 0 else None
    if isinstance(val, list):
        for item in val:
            m = parse_minutes(item)
            if m is not None:
                return m
        return None
    s = str(val).strip()
    if not s:
        return None
    # ISO-8601 duration (e.g. PT45M, PT1H30M, P0DT1H)
    if s.upper().startswith("P"):
        try:
            dur = isodate.parse_duration(s)
            secs = dur.total_seconds()
            return int(round(secs / 60)) if secs > 0 else None
        except Exception:
            pass
    # Human string (e.g. "1 hr 30 mins", "2 hours", "45 minutes")
    total = 0
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:days?|d\b)", s, re.I):
        total += int(round(float(m.group(1)) * 1440))
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|hr|h\b)", s, re.I):
        total += int(round(float(m.group(1)) * 60))
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|min|m\b)", s, re.I):
        total += int(round(float(m.group(1))))
    if total > 0:
        return total
    if s.isdigit() and int(s) > 0:
        return int(s)
    return None


def clean_str(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, list):
        parts = [norm(x) for x in val if x and norm(x)]
        s = ", ".join(parts)
    else:
        s = norm(val)
    if not s or s.lower() == "unknown":
        return None
    return s


def split_tags(items) -> list[str]:
    out = []
    seen = set()
    for raw in items:
        if not raw:
            continue
        for part in re.split(r"[,;/]", str(raw)):
            t = norm(part).lower()
            if t and 1 < len(t) <= 60 and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def model_to_record(
    m: dict,
    source_key: str,
    kind: str,
    meta: dict,
    site: str,
    ai_allowed: bool,
) -> RecipeRecord:
    title = clean_str(m.get("name")) or clean_str(meta.get("title")) or source_key
    author = clean_str(m.get("Author")) or clean_str(meta.get("author"))
    instructions = [{"heading": h, "steps": steps} for h, steps in (m.get("sections") or []) if steps]
    notes = [n for n in (m.get("notes") or []) if n]
    ingredients = [i for i in (m.get("ingredients") or []) if i]

    raw_parts = [title]
    if m.get("Description"):
        raw_parts.extend(m["Description"])
    raw_parts.extend(ingredients)
    for sec in instructions:
        if sec["heading"]:
            raw_parts.append(sec["heading"])
        raw_parts.extend(sec["steps"])
    raw_parts.extend(notes)

    tags = split_tags((m.get("Category") or []) + (m.get("Cuisine") or []) + (m.get("Keywords") or []))

    prep = parse_minutes(m.get("Prep time"))
    cook = parse_minutes(m.get("Cook time"))
    total = parse_minutes(m.get("Total time"))
    if total is None and (prep or cook):
        total = (prep or 0) + (cook or 0)

    return RecipeRecord(
        source_key=source_key,
        kind=kind,
        structured=True,
        title=title,
        author=author,
        source_site=site,
        source_url=meta.get("final_url") or meta.get("url"),
        yield_text=clean_str(m.get("Yield")),
        prep_minutes=prep,
        cook_minutes=cook,
        total_minutes=total,
        cuisine=clean_str(m.get("Cuisine")),
        category=clean_str(m.get("Category")),
        description=clean_str(m.get("Description")),
        instructions=instructions,
        source_notes=notes,
        raw_text="\n".join(raw_parts),
        ingredients=ingredients,
        tags=tags,
        ai_allowed=ai_allowed,
    )


def extract_web_unit(d: Path, meta: dict, ai_policy: dict[str, bool]) -> list[RecipeRecord]:
    site = meta.get("site") or d.parent.name
    slug = d.name
    base_key = f"web/{site}/{slug}"
    ai_allowed = ai_policy.get(site, True)
    method = meta.get("method")

    notes = []
    np = d / "recipe.notes.json"
    if np.is_file():
        try:
            notes = json.loads(np.read_text(encoding="utf-8")) or []
        except ValueError:
            notes = []

    if method == "jsonld":
        n = int(meta.get("recipe_count") or 1)
        out = []
        for i in range(n):
            fname = "recipe.jsonld" if i == 0 else f"recipe-{i + 1}.jsonld"
            fp = d / fname
            if not fp.is_file():
                continue
            obj = json.loads(fp.read_text(encoding="utf-8"))
            m = rd.model_from_jsonld(obj, notes if i == n - 1 else None)
            out.append(model_to_record(m, f"{base_key}#{i}", "web", meta, site, ai_allowed))
        return out

    if method == "card":
        card = json.loads((d / "recipe.card.json").read_text(encoding="utf-8"))
        first = {k: v for k, v in card.items() if k != "more_cards"}
        cards = [first] + list(card.get("more_cards") or [])
        return [
            model_to_record(rd.model_from_card(c), f"{base_key}#{i}", "web", meta, site, ai_allowed)
            for i, c in enumerate(cards)
        ]

    if method == "microdata":
        fields = json.loads((d / "recipe.fields.json").read_text(encoding="utf-8"))
        m = rd.model_from_fields(fields, notes)
        return [model_to_record(m, f"{base_key}#0", "web", meta, site, ai_allowed)]

    # Unstructured fallback (article.txt or page.txt)
    text_p = (d / "article.txt") if (d / "article.txt").is_file() else (d / "page.txt")
    raw_text = text_p.read_text(encoding="utf-8") if text_p.is_file() else ""
    title = clean_str(meta.get("title")) or slug.replace("-", " ").title()
    return [
        RecipeRecord(
            source_key=f"{base_key}#0",
            kind="web",
            structured=False,
            title=title,
            author=clean_str(meta.get("author")),
            source_site=site,
            source_url=meta.get("final_url") or meta.get("url"),
            raw_text=raw_text,
            ai_allowed=ai_allowed,
        )
    ]


def parse_house_markdown(md: str) -> tuple[list[str], list[dict], list[str]]:
    """Split loose Markdown from house app into ingredients, steps, and notes."""
    ingredients, steps, notes = [], [], []
    section = None
    for raw_line in md.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            hdr = line.lstrip("#").strip().lower()
            if any(k in hdr for k in ("ingredient", "you'll need", "you will need", "need")):
                section = "ingredients"
            elif any(k in hdr for k in ("method", "instruction", "direction", "step", "prep")):
                section = "steps"
            elif "note" in hdr or "tip" in hdr:
                section = "notes"
            else:
                section = "steps"
            continue
        if section == "ingredients" or (section is None and re.match(r"^[\-\*•]\s+", line)):
            item = re.sub(r"^[\-\*•]\s+", "", line).strip()
            if item:
                ingredients.append(item)
        elif section == "notes":
            notes.append(re.sub(r"^[\-\*•>]\s+", "", line).strip())
        else:
            step = re.sub(r"^(?:\d+[\.\)]\s+|[\-\*•]\s+)", "", line).strip()
            if step:
                steps.append(step)
    instructions = [{"heading": None, "steps": steps}] if steps else []
    return ingredients, instructions, notes


def extract_house_unit(d: Path, meta: dict) -> list[RecipeRecord]:
    rec_wrap = json.loads((d / "record.json").read_text(encoding="utf-8"))
    rec = rec_wrap.get("recipe") or {}
    body_en = rec.get("bodyEn") or (d / "recipe.md").read_text(encoding="utf-8")
    ingredients, instructions, notes = parse_house_markdown(body_en)
    if rec.get("noteEn"):
        notes.append(norm(rec["noteEn"]))
    tags = split_tags([rec.get("tagEn")])
    title = clean_str(rec.get("titleEn")) or clean_str(meta.get("title")) or d.name
    return [
        RecipeRecord(
            source_key=f"house/{d.name}",
            kind="house",
            structured=bool(ingredients or instructions),
            title=title,
            author=clean_str(rec.get("source")),
            source_site="house",
            source_url=meta.get("url"),
            total_minutes=parse_minutes(rec.get("minutes")),
            instructions=instructions,
            source_notes=notes,
            raw_text=body_en,
            ingredients=ingredients,
            tags=tags,
            ai_allowed=True,
        )
    ]


def extract_pdf_unit(d: Path, meta: dict, ai_policy: dict[str, bool]) -> list[RecipeRecord]:
    site = d.parent.name
    texts = []
    for name in ("document.txt", "document.ocr.txt"):
        p = d / name
        if p.is_file():
            t = p.read_text(encoding="utf-8").strip()
            if t:
                texts.append(t)
    raw_text = "\n\n".join(texts)
    title = clean_str(meta.get("title")) or clean_str(meta.get("listing_label")) or d.name
    return [
        RecipeRecord(
            source_key=f"pdf/{site}/{d.name}",
            kind="pdf",
            structured=False,
            title=title,
            author=clean_str(meta.get("author")),
            source_site=site,
            source_url=meta.get("final_url") or meta.get("url"),
            raw_text=raw_text,
            ai_allowed=ai_policy.get(site, True),
        )
    ]


def collect_records(library_dir: Path, ai_policy: dict[str, bool]) -> list[RecipeRecord]:
    records = []
    for mp in sorted(library_dir.rglob("meta.json")):
        d = mp.parent
        meta = json.loads(mp.read_text(encoding="utf-8"))
        kind = meta.get("kind")
        if kind == "web":
            records.extend(extract_web_unit(d, meta, ai_policy))
        elif kind == "house":
            records.extend(extract_house_unit(d, meta))
        elif kind == "pdf":
            records.extend(extract_pdf_unit(d, meta, ai_policy))
    return records


def upsert_all(conn: psycopg.Connection, records: list[RecipeRecord]) -> dict[str, int]:
    stats = {"recipes": 0, "ingredients": 0, "recipe_ingredients": 0, "tags": 0}

    # 1. Pre-parse all ingredients & gather unique canonical names
    parsed_per_recipe = []
    unique_canon: dict[str, bool] = {}
    for rec in records:
        parsed = [parse_ingredient(raw) for raw in rec.ingredients]
        parsed_per_recipe.append(parsed)
        for p in parsed:
            if p.canonical and p.canonical not in unique_canon:
                unique_canon[p.canonical] = is_staple_name(p.canonical)

    with conn.cursor() as cur:
        # 2. Bulk upsert canonical ingredients (preserving preferred_brand & notes)
        if unique_canon:
            cur.executemany(
                """
                INSERT INTO ingredients (name, is_staple)
                VALUES (%s, %s)
                ON CONFLICT (name) DO NOTHING
                """,
                list(unique_canon.items()),
            )
            cur.execute("SELECT name, id FROM ingredients")
            ing_id_map = {name: iid for name, iid in cur.fetchall()}
        else:
            ing_id_map = {}
        stats["ingredients"] = len(ing_id_map)

        # 3. Upsert recipes (preserving user rating, effort, times_cooked, last_cooked_at)
        recipe_ids = []
        for rec in records:
            cur.execute(
                """
                INSERT INTO recipes (
                    source_key, kind, structured, title, author, source_site, source_url,
                    yield_text, prep_minutes, cook_minutes, total_minutes,
                    cuisine, category, description, instructions, source_notes,
                    raw_text, ai_allowed, updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, NOW()
                )
                ON CONFLICT (source_key) DO UPDATE SET
                    kind = EXCLUDED.kind,
                    structured = EXCLUDED.structured,
                    title = EXCLUDED.title,
                    author = EXCLUDED.author,
                    source_site = EXCLUDED.source_site,
                    source_url = EXCLUDED.source_url,
                    yield_text = EXCLUDED.yield_text,
                    prep_minutes = EXCLUDED.prep_minutes,
                    cook_minutes = EXCLUDED.cook_minutes,
                    total_minutes = EXCLUDED.total_minutes,
                    cuisine = EXCLUDED.cuisine,
                    category = EXCLUDED.category,
                    description = EXCLUDED.description,
                    instructions = EXCLUDED.instructions,
                    source_notes = EXCLUDED.source_notes,
                    raw_text = EXCLUDED.raw_text,
                    ai_allowed = EXCLUDED.ai_allowed,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    rec.source_key, rec.kind, rec.structured, rec.title, rec.author,
                    rec.source_site, rec.source_url, rec.yield_text,
                    rec.prep_minutes, rec.cook_minutes, rec.total_minutes,
                    rec.cuisine, rec.category, rec.description,
                    Jsonb(rec.instructions), Jsonb(rec.source_notes),
                    rec.raw_text, rec.ai_allowed,
                ),
            )
            recipe_ids.append(cur.fetchone()[0])
        stats["recipes"] = len(recipe_ids)

        # 4. Replace recipe_ingredients & source tags for imported recipes in batch
        cur.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ANY(%s)", (recipe_ids,))
        cur.execute("DELETE FROM recipe_tags WHERE recipe_id = ANY(%s) AND origin = 'source'", (recipe_ids,))

        ri_rows = []
        tag_rows = []
        for rid, rec, parsed in zip(recipe_ids, records, parsed_per_recipe):
            for pos, p in enumerate(parsed):
                ri_rows.append((
                    rid,
                    ing_id_map.get(p.canonical) if p.canonical else None,
                    pos,
                    p.raw_text,
                    p.quantity,
                    p.unit,
                    p.prep,
                    p.optional,
                ))
            for t in rec.tags:
                tag_rows.append((rid, t, "source"))

        if ri_rows:
            cur.executemany(
                """
                INSERT INTO recipe_ingredients
                    (recipe_id, ingredient_id, position, raw_text, quantity, unit, prep, optional)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                ri_rows,
            )
            stats["recipe_ingredients"] = len(ri_rows)

        if tag_rows:
            cur.executemany(
                """
                INSERT INTO recipe_tags (recipe_id, tag, origin)
                VALUES (%s, %s, %s)
                ON CONFLICT (recipe_id, tag) DO NOTHING
                """,
                tag_rows,
            )
            stats["tags"] = len(tag_rows)

        # 5. Bulk compute full-text search vectors in one SQL pass
        cur.execute(
            """
            UPDATE recipes r
            SET search_vector =
                setweight(to_tsvector('english', COALESCE(r.title, '')), 'A') ||
                setweight(to_tsvector('english', COALESCE(r.author, '') || ' ' || COALESCE(r.cuisine, '') || ' ' || COALESCE(r.category, '')), 'B') ||
                setweight(to_tsvector('english', COALESCE(ing.txt, '')), 'B') ||
                setweight(to_tsvector('english', COALESCE(tg.txt, '')), 'B') ||
                setweight(to_tsvector('english', COALESCE(fb.txt, '')), 'B') ||
                setweight(to_tsvector('english', COALESCE(r.description, '') || ' ' || LEFT(COALESCE(r.raw_text, ''), 20000)), 'C')
            FROM (SELECT unnest(%s::bigint[]) AS id) ids
            LEFT JOIN LATERAL (
                SELECT string_agg(COALESCE(i.name, '') || ' ' || ri.raw_text, ' ') AS txt
                FROM recipe_ingredients ri
                LEFT JOIN ingredients i ON i.id = ri.ingredient_id
                WHERE ri.recipe_id = ids.id
            ) ing ON TRUE
            LEFT JOIN LATERAL (
                SELECT string_agg(rt.tag, ' ') AS txt
                FROM recipe_tags rt
                WHERE rt.recipe_id = ids.id
            ) tg ON TRUE
            LEFT JOIN LATERAL (
                SELECT string_agg(rf.comment, ' ') AS txt
                FROM recipe_feedback rf
                WHERE rf.recipe_id = ids.id
            ) fb ON TRUE
            WHERE r.id = ids.id
            """,
            (recipe_ids,),
        )
    conn.commit()
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    ap.add_argument("--env-file", type=Path, default=PROJECT_DIR / ".env.local")
    args = ap.parse_args()

    if args.env_file.is_file():
        load_dotenv(args.env_file)
    db_url = os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("Missing DATABASE_URL_UNPOOLED / DATABASE_URL in environment or .env.local")

    ai_policy = load_ai_policy()
    records = collect_records(args.library, ai_policy)
    print(f"Collected {len(records)} recipe records from {args.library}")

    by_site: dict[str, int] = {}
    for r in records:
        k = f"{r.kind}/{r.source_site} (ai_allowed={r.ai_allowed})"
        by_site[k] = by_site.get(k, 0) + 1
    for k in sorted(by_site):
        print(f"  {k}: {by_site[k]}")

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        print("Schema applied.")

        stats = upsert_all(conn, records)
        print(
            f"Ingested: recipes={stats['recipes']}, "
            f"canonical_ingredients={stats['ingredients']}, "
            f"recipe_ingredients={stats['recipe_ingredients']}, "
            f"tags={stats['tags']}"
        )


if __name__ == "__main__":
    main()
