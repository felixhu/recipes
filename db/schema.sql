-- Normalized Postgres schema for the recipe database.
-- Idempotent: safe to re-run on an existing database.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 1. Uniform recipe table (web JSON-LD, HTML cards, unstructured articles, PDFs, house app, books)
CREATE TABLE IF NOT EXISTS recipes (
    id              BIGSERIAL PRIMARY KEY,
    source_key      TEXT UNIQUE NOT NULL,          -- e.g. 'web/kingarthur/fudge-brownies-recipe#0'
    kind            TEXT NOT NULL,                 -- 'web' | 'pdf' | 'house' | 'book' | 'manual'
    structured      BOOLEAN NOT NULL DEFAULT TRUE, -- FALSE for raw text/PDF without structured steps
    title           TEXT NOT NULL,
    author          TEXT,
    source_site     TEXT,                          -- e.g. 'kingarthur', 'edibleschoolyard', 'house'
    source_url      TEXT,
    yield_text      TEXT,
    prep_minutes    INTEGER,
    cook_minutes    INTEGER,
    total_minutes   INTEGER,
    cuisine         TEXT,
    category        TEXT,
    description     TEXT,
    instructions    JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{"heading": null|"...", "steps": ["..."]}]
    source_notes    JSONB NOT NULL DEFAULT '[]'::jsonb,  -- publisher's cook's notes
    raw_text        TEXT,                          -- full searchable text (or article/OCR text)
    rating          SMALLINT CHECK (rating BETWEEN 1 AND 5),
    effort          TEXT CHECK (effort IN ('easy', 'medium', 'project')),
    times_cooked    INTEGER NOT NULL DEFAULT 0,
    last_cooked_at  TIMESTAMPTZ,
    ai_allowed      BOOLEAN NOT NULL DEFAULT TRUE, -- FALSE for sources like theguardian
    search_vector   TSVECTOR,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recipes_source_site ON recipes (source_site);
CREATE INDEX IF NOT EXISTS idx_recipes_ai_allowed ON recipes (ai_allowed);
CREATE INDEX IF NOT EXISTS idx_recipes_total_minutes ON recipes (total_minutes);
CREATE INDEX IF NOT EXISTS idx_recipes_effort ON recipes (effort);
CREATE INDEX IF NOT EXISTS idx_recipes_rating ON recipes (rating);
CREATE INDEX IF NOT EXISTS idx_recipes_search_vector ON recipes USING GIN (search_vector);
CREATE INDEX IF NOT EXISTS idx_recipes_title_trgm ON recipes USING GIN (title gin_trgm_ops);

-- 2. Normalized pantry / canonical ingredient dictionary (with brand preferences!)
CREATE TABLE IF NOT EXISTS ingredients (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,          -- canonical lowercase name, e.g. 'chicken thigh'
    is_staple       BOOLEAN NOT NULL DEFAULT FALSE,
    preferred_brand TEXT,                          -- e.g. 'Mutti or Bianco DiNapoli', 'Ortiz'
    notes           TEXT,                          -- e.g. 'always buy unsalted'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingredients_name_trgm ON ingredients USING GIN (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_ingredients_is_staple ON ingredients (is_staple);

-- 3. Recipe <-> normalized ingredients
CREATE TABLE IF NOT EXISTS recipe_ingredients (
    id              BIGSERIAL PRIMARY KEY,
    recipe_id       BIGINT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    ingredient_id   BIGINT REFERENCES ingredients(id) ON DELETE SET NULL,
    position        INTEGER NOT NULL,
    raw_text        TEXT NOT NULL,
    quantity        DOUBLE PRECISION,
    unit            TEXT,
    prep            TEXT,
    optional        BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_recipe ON recipe_ingredients (recipe_id);
CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_ingredient ON recipe_ingredients (ingredient_id);

-- 4. Tags (source keywords + your own tags like 'easy', 'weeknight', 'kid-friendly')
CREATE TABLE IF NOT EXISTS recipe_tags (
    recipe_id       BIGINT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    tag             TEXT NOT NULL,
    origin          TEXT NOT NULL DEFAULT 'user' CHECK (origin IN ('source', 'user')),
    PRIMARY KEY (recipe_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_recipe_tags_tag ON recipe_tags (tag);

-- 5. Per-recipe feedback, tweaks & cook log
CREATE TABLE IF NOT EXISTS recipe_feedback (
    id              BIGSERIAL PRIMARY KEY,
    recipe_id       BIGINT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL DEFAULT 'note' CHECK (kind IN ('note', 'tweak', 'review', 'log')),
    comment         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recipe_feedback_recipe ON recipe_feedback (recipe_id);

-- 6. Global user preferences (taste defaults, equipment, shopping rules)
CREATE TABLE IF NOT EXISTS user_preferences (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Helper function to rebuild search_vector for one recipe (includes title, ingredients, tags, feedback)
CREATE OR REPLACE FUNCTION refresh_recipe_search_vector(p_recipe_id BIGINT) RETURNS VOID AS $$
BEGIN
    UPDATE recipes r
    SET search_vector =
        setweight(to_tsvector('english', COALESCE(r.title, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(r.author, '') || ' ' || COALESCE(r.cuisine, '') || ' ' || COALESCE(r.category, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE((
            SELECT string_agg(COALESCE(i.name, '') || ' ' || ri.raw_text, ' ')
            FROM recipe_ingredients ri
            LEFT JOIN ingredients i ON i.id = ri.ingredient_id
            WHERE ri.recipe_id = r.id
        ), '')), 'B') ||
        setweight(to_tsvector('english', COALESCE((
            SELECT string_agg(rt.tag, ' ')
            FROM recipe_tags rt
            WHERE rt.recipe_id = r.id
        ), '')), 'B') ||
        setweight(to_tsvector('english', COALESCE((
            SELECT string_agg(rf.comment, ' ')
            FROM recipe_feedback rf
            WHERE rf.recipe_id = r.id
        ), '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(r.description, '') || ' ' || LEFT(COALESCE(r.raw_text, ''), 20000)), 'C')
    WHERE r.id = p_recipe_id;
END;
$$ LANGUAGE plpgsql;
