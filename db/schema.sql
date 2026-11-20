-- DerKaiser: PostgreSQL schema for jobs found at Romanian firms. Idempotent (safe to run again).
--
-- GDPR / data minimisation: only what is needed to link a job to its source.
-- There is deliberately NO column for the job description, CVs or any personal data.

CREATE TABLE IF NOT EXISTS companies (
    cif        text PRIMARY KEY CHECK (cif ~ '^[0-9]{2,10}$'),   -- CIF/CUI, digits only, zero-padded to 8
    name       text NOT NULL CHECK (btrim(name) <> ''),          -- legal name
    website    text,
    employees  integer CHECK (employees IS NULL OR employees >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobs (
    id         bigserial PRIMARY KEY,
    url        text NOT NULL UNIQUE CHECK (url ~ '^https?://'),  -- canonical link to the employer's / source's ad
    title      text NOT NULL CHECK (btrim(title) <> ''),
    cif        text REFERENCES companies (cif) ON UPDATE CASCADE,   -- NULL for ads whose employer has no CIF in our lists
    company_name text NOT NULL CHECK (btrim(company_name) <> ''),     -- employer as written by the source
    country    text NOT NULL DEFAULT 'RO',
    city       text,                                             -- NULL when the ad only says "Romania"
    county     text,
    work_mode  text CHECK (work_mode IN ('remote', 'hybrid', 'on-site')),
    source     text NOT NULL DEFAULT 'unknown',                  -- anofm, posturi.gov.ro, greenhouse, site, ...
    expires_at timestamptz,
    scraped_at timestamptz,
    first_seen timestamptz NOT NULL DEFAULT now(),
    last_seen  timestamptz NOT NULL DEFAULT now(),
    active     boolean NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS jobs_cif_idx         ON jobs (cif);
CREATE INDEX IF NOT EXISTS jobs_active_city_idx ON jobs (active, city);
CREATE INDEX IF NOT EXISTS jobs_source_idx      ON jobs (source);
CREATE INDEX IF NOT EXISTS jobs_company_name_idx ON jobs (company_name);

-- What a front end should read: live jobs only, with the company name.
CREATE OR REPLACE VIEW active_jobs AS
SELECT j.id, j.url, j.title, j.cif, COALESCE(c.name, j.company_name) AS company, j.city, j.county, j.work_mode, j.source, j.expires_at, j.last_seen
FROM jobs j
LEFT JOIN companies c ON c.cif = j.cif
WHERE j.active AND (j.expires_at IS NULL OR j.expires_at > now());
