-- OPTIONAL, run after schema.sql / import.sql: search that ignores diacritics and case (PostgreSQL 13+).
--   psql -d NUME_BAZA -f db/search.sql
--   SELECT * FROM search_jobs('asistent medical pitesti');
-- Needs the extensions unaccent and pg_trgm. Both are "trusted": the owner of the database can create them,
-- no superuser needed on PostgreSQL 13+. Kept apart from schema.sql so a missing extension never breaks the import.

CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- unaccent() is only STABLE; an index needs an IMMUTABLE wrapper.
CREATE OR REPLACE FUNCTION immutable_unaccent(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS search_text text GENERATED ALWAYS AS (
    lower(immutable_unaccent(title || ' ' || company_name || ' ' || coalesce(city, '') || ' ' || coalesce(county, '')))
) STORED;
CREATE INDEX IF NOT EXISTS jobs_search_trgm_idx ON jobs USING gin (search_text gin_trgm_ops);

-- Every word typed must appear somewhere in title / company / city / county. Newest first.
CREATE OR REPLACE FUNCTION search_jobs(q text, max_rows integer DEFAULT 50)
RETURNS TABLE (id bigint, url text, title text, company text, city text, county text, source text)
LANGUAGE sql STABLE
AS $$
    SELECT j.id, j.url, j.title, COALESCE(c.name, j.company_name), j.city, j.county, j.source
    FROM jobs j
    LEFT JOIN companies c ON c.cif = j.cif
    WHERE j.active AND (j.expires_at IS NULL OR j.expires_at > now())
      AND j.search_text LIKE ALL (ARRAY(
            SELECT '%' || w || '%'
            FROM unnest(regexp_split_to_array(lower(immutable_unaccent(q)), '\s+')) AS w
            WHERE w <> ''))
    ORDER BY j.last_seen DESC, j.id DESC
    LIMIT max_rows
$$;
