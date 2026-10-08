-- Core schema. Applied once by app/migrate.py (tracked in schema_migrations).

CREATE TABLE upload_jobs (
    id                    UUID PRIMARY KEY,
    filename              TEXT        NOT NULL,
    s3_key                TEXT        NOT NULL UNIQUE,
    file_hash             TEXT,
    -- PENDING -> PROCESSING -> COMPLETED | COMPLETED_WITH_ERRORS | FAILED | DUPLICATE
    status                TEXT        NOT NULL DEFAULT 'PENDING',
    total_rows            INTEGER     NOT NULL DEFAULT 0,
    valid_rows            INTEGER     NOT NULL DEFAULT 0,
    rejected_rows         INTEGER     NOT NULL DEFAULT 0,
    error_key             TEXT,
    error_message         TEXT,
    duplicate_of          UUID REFERENCES upload_jobs (id),
    -- set by n8n Workflow B: NOT_STARTED -> RUNNING -> DONE | FAILED
    matching_status       TEXT        NOT NULL DEFAULT 'NOT_STARTED',
    matching_execution_id TEXT,         -- n8n execution id, lets the error workflow mark the right job
    webhook_attempts      INTEGER     NOT NULL DEFAULT 0,
    webhook_delivered_at  TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at           TIMESTAMPTZ
);
-- A finished upload with the same content is never processed twice.
CREATE UNIQUE INDEX upload_jobs_file_hash_done
    ON upload_jobs (file_hash)
    WHERE status IN ('COMPLETED', 'COMPLETED_WITH_ERRORS');

CREATE TABLE users (
    user_id            TEXT PRIMARY KEY,
    name               TEXT,
    -- email is NOT unique: users.csv has 10k users but only ~2.6k distinct emails
    email              TEXT        NOT NULL,
    monthly_income     NUMERIC(12, 2) NOT NULL,
    credit_score       INTEGER     NOT NULL,
    employment_status  TEXT        NOT NULL,
    age                INTEGER     NOT NULL,
    upload_job_id      UUID        NOT NULL REFERENCES upload_jobs (id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX users_upload_job_idx ON users (upload_job_id, user_id);
CREATE INDEX users_score_income_idx ON users (credit_score, monthly_income);
CREATE INDEX users_email_idx ON users (email);

CREATE TABLE loan_products (
    id                  BIGSERIAL PRIMARY KEY,
    source              TEXT        NOT NULL,
    product_name        TEXT        NOT NULL,
    interest_rate       NUMERIC(5, 2),
    min_income          NUMERIC(12, 2),
    min_credit_score    INTEGER,
    max_credit_score    INTEGER,
    min_age             INTEGER,
    max_age             INTEGER,
    allowed_employment  TEXT[],          -- NULL = no restriction
    criteria_text       TEXT,            -- raw eligibility wording, if any
    -- true when structured criteria could not be extracted: only an LLM can judge
    needs_review        BOOLEAN     NOT NULL DEFAULT false,
    active              BOOLEAN     NOT NULL DEFAULT true,
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, product_name)
);

CREATE TABLE crawl_state (
    source           TEXT PRIMARY KEY,
    content_hash     TEXT,
    last_crawled_at  TIMESTAMPTZ,
    last_status      TEXT
);

CREATE TABLE matches (
    id             BIGSERIAL PRIMARY KEY,
    upload_job_id  UUID        NOT NULL REFERENCES upload_jobs (id),
    user_id        TEXT        NOT NULL REFERENCES users (user_id),
    product_id     BIGINT      NOT NULL REFERENCES loan_products (id),
    score          NUMERIC(6, 2) NOT NULL DEFAULT 0,
    match_stage    TEXT        NOT NULL,   -- 'rules' | 'rules_llm' (criteria extracted by the LLM)
    reasons        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    notified_at    TIMESTAMPTZ,
    UNIQUE (upload_job_id, user_id, product_id)
);
CREATE INDEX matches_pending_notify_idx ON matches (upload_job_id) WHERE notified_at IS NULL;

-- LLM output cache: free-text eligibility wording -> structured thresholds (JSON).
-- Keyed by md5(criteria_text), so each distinct wording is sent to the LLM only once, ever.
CREATE TABLE llm_cache (
    cache_key   TEXT PRIMARY KEY,
    result      JSONB       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Effective criteria used by matching: structured columns first, LLM-extracted values as fallback.
-- Products that need review but have no cached extraction yet are excluded (never guessed).
CREATE VIEW product_criteria AS
SELECT p.id, p.source, p.product_name, p.interest_rate, p.needs_review AS via_llm,
       COALESCE(p.min_income, (c.result ->> 'min_income')::numeric)           AS min_income,
       COALESCE(p.min_credit_score, (c.result ->> 'min_credit_score')::int)   AS min_credit_score,
       COALESCE(p.max_credit_score, (c.result ->> 'max_credit_score')::int)   AS max_credit_score,
       COALESCE(p.min_age, (c.result ->> 'min_age')::int)                     AS min_age,
       COALESCE(p.max_age, (c.result ->> 'max_age')::int)                     AS max_age,
       COALESCE(p.allowed_employment,
                CASE WHEN jsonb_typeof(c.result -> 'allowed_employment') = 'array'
                          AND jsonb_array_length(c.result -> 'allowed_employment') > 0
                     THEN ARRAY(SELECT jsonb_array_elements_text(c.result -> 'allowed_employment')) END
       ) AS allowed_employment
FROM loan_products p
LEFT JOIN llm_cache c ON c.cache_key = md5(p.criteria_text)
WHERE p.active AND (NOT p.needs_review OR c.cache_key IS NOT NULL);

-- One row per matching run: numbers for the optimisation funnel.
CREATE TABLE funnel_metrics (
    upload_job_id     UUID PRIMARY KEY REFERENCES upload_jobs (id),
    users_count       INTEGER NOT NULL DEFAULT 0,
    products_count    INTEGER NOT NULL DEFAULT 0,   -- active products with usable criteria
    pairs_naive       BIGINT  NOT NULL DEFAULT 0,   -- users x products
    pairs_after_sql   BIGINT  NOT NULL DEFAULT 0,   -- survivors of the SQL pre-filter
    matches_rules     BIGINT  NOT NULL DEFAULT 0,   -- matches written after scoring / top-K
    llm_products      INTEGER NOT NULL DEFAULT 0,   -- distinct free-text criteria needing the LLM
    llm_calls         INTEGER NOT NULL DEFAULT 0,   -- LLM calls actually made (cache misses)
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ
);
