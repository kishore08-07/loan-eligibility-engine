#!/usr/bin/env python3
"""Generates n8n/workflows/*.json from the SQL below and the Code-node scripts in n8n/code/.

Keeping the JavaScript in real .js files makes it reviewable; this script only assembles the
workflow JSON that n8n imports. Re-run after editing:  python3 n8n/build_workflows.py
"""
import json
import pathlib
import uuid

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "workflows"
OUT.mkdir(exist_ok=True)

PG = {"postgres": {"id": "pgcred", "name": "Loans Postgres"}}
SMTP = {"smtp": {"id": "smtpcred", "name": "Local Mailpit SMTP"}}
AWS = {"aws": {"id": "awscred", "name": "AWS SES"}}


def code(name):
    """Read a Code-node script, inlining the shared helper where it says `// @include _llm.js`."""
    text = (HERE / "code" / name).read_text()
    return text.replace("// @include _llm.js", (HERE / "code" / "_llm.js").read_text())


class Flow:
    def __init__(self, wf_id, name, error_workflow=None):
        self.id, self.name, self.error_workflow = wf_id, name, error_workflow
        self.nodes, self.conns, self._x = [], {}, 0

    def add(self, name, type_, version, params=None, creds=None, at=None, **extra):
        self._x += 1
        node = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.id}/{name}")), "name": name, "type": type_,
                "typeVersion": version, "position": at or [240 * self._x, 300], "parameters": params or {}}
        if creds:
            node["credentials"] = creds
        node.update(extra)
        self.nodes.append(node)
        return name

    def connect(self, src, dst, output=0):
        outs = self.conns.setdefault(src, {"main": []})["main"]
        while len(outs) <= output:
            outs.append([])
        outs[output].append({"node": dst, "type": "main", "index": 0})

    def chain(self, *names):
        for a, b in zip(names, names[1:]):
            self.connect(a, b)

    def write(self):
        settings = {"executionOrder": "v1"}
        if self.error_workflow:
            settings["errorWorkflow"] = self.error_workflow
        doc = {"id": self.id, "name": self.name, "nodes": self.nodes, "connections": self.conns,
               "settings": settings, "pinData": {}}
        (OUT / f"{self.id}.json").write_text(json.dumps(doc, indent=2) + "\n")


def pg(f, name, query, replacement=None, always=False, retry=True, once=False):
    params = {"operation": "executeQuery", "query": query, "options": {}}
    if replacement:
        params["options"]["queryReplacement"] = replacement
    extra = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 2000} if retry else {}
    if always:
        extra["alwaysOutputData"] = True
    if once:
        extra["executeOnce"] = True
    return f.add(name, "n8n-nodes-base.postgres", 2.5, params, PG, **extra)


def code_node(f, name, script, mode="runOnceForAllItems"):
    return f.add(name, "n8n-nodes-base.code", 2, {"jsCode": code(script), "mode": mode})


# --------------------------------------------------------------------------- E: error handler
def build_error():
    f = Flow("wfE_error", "E - Error handler")
    f.add("Error Trigger", "n8n-nodes-base.errorTrigger", 1)
    pg(f, "Mark Matching Failed",
       "UPDATE upload_jobs SET matching_status = 'FAILED', updated_at = now() "
       "WHERE matching_execution_id = $1 AND matching_status = 'RUNNING' RETURNING id",
       "={{ [String($json.execution.id)] }}", retry=False)
    f.chain("Error Trigger", "Mark Matching Failed")
    f.write()


# --------------------------------------------------------------------------- B: matching
START_RUN = """
WITH job AS (
  UPDATE upload_jobs SET matching_status = 'RUNNING', matching_execution_id = $2, updated_at = now()
  WHERE id = $1::uuid AND status IN ('COMPLETED', 'COMPLETED_WITH_ERRORS') RETURNING id
), cleared AS (   -- a re-run replaces matches that were not emailed yet
  DELETE FROM matches WHERE upload_job_id = $1::uuid AND notified_at IS NULL AND EXISTS (SELECT 1 FROM job)
), cnt AS (
  SELECT count(*) AS users FROM users WHERE upload_job_id = $1::uuid
), m AS (
  INSERT INTO funnel_metrics (upload_job_id, users_count)
  SELECT job.id, cnt.users FROM job, cnt
  ON CONFLICT (upload_job_id) DO UPDATE SET users_count = EXCLUDED.users_count, products_count = 0,
    pairs_naive = 0, pairs_after_sql = 0, matches_rules = 0, llm_products = 0, llm_calls = 0,
    started_at = now(), finished_at = NULL
)
SELECT (SELECT count(*) FROM job) AS job_found, (SELECT users FROM cnt) AS users_count
""".strip()

FIND_REVIEW = """
SELECT p.id AS product_id, p.product_name, p.criteria_text, md5(p.criteria_text) AS cache_key
FROM loan_products p
LEFT JOIN llm_cache c ON c.cache_key = md5(p.criteria_text)
WHERE p.active AND p.needs_review AND p.criteria_text IS NOT NULL AND c.cache_key IS NULL
""".strip()

SAVE_CACHE = """
WITH ins AS (
  INSERT INTO llm_cache (cache_key, result)
  SELECT r.cache_key, r.result FROM jsonb_to_recordset($2::jsonb) AS r(cache_key text, result jsonb)
  ON CONFLICT (cache_key) DO NOTHING RETURNING 1
)
UPDATE funnel_metrics f SET llm_calls = $3::int,
  llm_products = (SELECT count(*) FROM loan_products WHERE active AND needs_review)
WHERE f.upload_job_id = $1::uuid
RETURNING f.upload_job_id
""".strip()

PREFILTER = """
WITH batch AS (
  SELECT user_id, monthly_income, credit_score, age, employment_status
  FROM users WHERE upload_job_id = $1::uuid ORDER BY user_id LIMIT $3::int OFFSET $2::int
), fit AS (   -- stage 1: hard criteria (set-based join) + how comfortably each pair clears them
  SELECT b.user_id, p.id AS product_id, p.via_llm,
         COALESCE(LEAST(GREATEST((b.monthly_income - p.min_income) / NULLIF(p.min_income, 0), 0), 1), 0.5) AS income_fit,
         COALESCE(LEAST(GREATEST((b.credit_score - p.min_credit_score) / 100.0, 0), 1), 0.5)              AS credit_fit,
         COALESCE(LEAST(GREATEST((20 - p.interest_rate) / 10.0, 0), 1), 0.5)                              AS rate_fit
  FROM batch b
  JOIN product_criteria p
    ON b.monthly_income >= COALESCE(p.min_income, 0)
   AND b.credit_score  >= COALESCE(p.min_credit_score, 0)
   AND b.credit_score  <= COALESCE(p.max_credit_score, 1000)
   AND b.age           >= COALESCE(p.min_age, 0)
   AND b.age           <= COALESCE(p.max_age, 200)
   AND (p.allowed_employment IS NULL OR b.employment_status = ANY (p.allowed_employment))
), ranked AS (   -- stage 2: weights come from n8n; keep only the top K products per user, inside Postgres
  SELECT f.*,
         round((100 * ($4::numeric * income_fit + $5::numeric * credit_fit + $6::numeric * rate_fit))::numeric, 2) AS score,
         row_number() OVER (PARTITION BY user_id
                            ORDER BY $4::numeric * income_fit + $5::numeric * credit_fit + $6::numeric * rate_fit DESC,
                                     product_id) AS rn,
         count(*) OVER () AS eligible_total
  FROM fit f
)
SELECT user_id, product_id, via_llm, score, income_fit, credit_fit, rate_fit, eligible_total
FROM ranked WHERE rn <= $7::int
""".strip()

INSERT_MATCHES = """
WITH ins AS (
  INSERT INTO matches (upload_job_id, user_id, product_id, score, match_stage, reasons)
  SELECT $1::uuid, r.user_id, r.product_id, r.score, r.match_stage, r.reasons
  FROM jsonb_to_recordset($2::jsonb)
       AS r(user_id text, product_id bigint, score numeric, match_stage text, reasons jsonb)
  ON CONFLICT (upload_job_id, user_id, product_id) DO NOTHING RETURNING 1
)
UPDATE funnel_metrics SET pairs_after_sql = pairs_after_sql + $3::bigint,
       matches_rules = matches_rules + (SELECT count(*) FROM ins)
WHERE upload_job_id = $1::uuid
RETURNING upload_job_id
""".strip()

FINALIZE = """
WITH j AS (UPDATE upload_jobs SET matching_status = 'DONE', updated_at = now()
           WHERE id = $1::uuid RETURNING id)
UPDATE funnel_metrics SET finished_at = now(),
  products_count = (SELECT count(*) FROM product_criteria),
  pairs_naive = users_count::bigint * (SELECT count(*) FROM product_criteria)
WHERE upload_job_id = $1::uuid
RETURNING upload_job_id, users_count, products_count, pairs_naive, pairs_after_sql, matches_rules, llm_calls
""".strip()


def build_matching():
    f = Flow("wfB_matching", "B - User-Loan Matching", error_workflow="wfE_error")
    f.add("Webhook", "n8n-nodes-base.webhook", 2,
          {"httpMethod": "POST", "path": "match-users", "responseMode": "responseNode",
           "options": {"rawBody": True}}, webhookId="match-users-wh")
    code_node(f, "Verify Signature", "b_verify_signature.js")
    f.add("Signature Valid?", "n8n-nodes-base.if", 2.2, {"conditions": {
        "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
        "conditions": [{"id": "valid", "leftValue": "={{ $json.valid }}", "rightValue": "",
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
        "combinator": "and"}, "options": {}})
    f.add("Respond 202", "n8n-nodes-base.respondToWebhook", 1.1,
          {"respondWith": "json", "responseBody": '={{ { "accepted": true, "upload_job_id": $json.upload_job_id } }}',
           "options": {"responseCode": 202}})
    f.add("Respond 401", "n8n-nodes-base.respondToWebhook", 1.1,
          {"respondWith": "json", "responseBody": '={{ { "error": "invalid signature" } }}',
           "options": {"responseCode": 401}})
    pg(f, "Start Run", START_RUN, "={{ [$('Verify Signature').first().json.upload_job_id, String($execution.id)] }}")
    # Stage 3 (before matching): LLM turns free-text criteria into thresholds, cached forever.
    pg(f, "Find Uncached Criteria", FIND_REVIEW, always=True)
    code_node(f, "LLM Normalise Criteria", "b_llm_normalise.js")
    pg(f, "Save LLM Cache", SAVE_CACHE,
       "={{ [$json.upload_job_id, JSON.stringify($json.rows), $json.llm_calls] }}")
    code_node(f, "Plan Batches", "b_plan_batches.js")
    f.add("Loop Batches", "n8n-nodes-base.splitInBatches", 3, {"batchSize": 1, "options": {}})
    pg(f, "SQL Pre-filter + Top-K", PREFILTER,
       "={{ [$json.upload_job_id, $json.offset, $json.batch_size, $json.w_income, $json.w_credit, $json.w_rate, $json.top_k] }}",
       always=True)
    code_node(f, "Format Matches", "b_format_matches.js")
    pg(f, "Insert Matches", INSERT_MATCHES,
       "={{ [$json.upload_job_id, JSON.stringify($json.rows), $json.pairs_after_sql] }}")
    pg(f, "Finalize", FINALIZE, "={{ [$('Verify Signature').first().json.upload_job_id] }}", once=True)
    f.add("Trigger Notifications", "n8n-nodes-base.httpRequest", 4.2, {
        "method": "POST", "url": "http://localhost:5678/webhook/notify-users",
        "sendHeaders": True, "headerParameters": {"parameters": [
            {"name": "X-Internal-Token", "value": "={{ $env.WEBHOOK_SECRET }}"}]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ upload_job_id: $json.upload_job_id }) }}",
        "options": {"timeout": 15000}}, retryOnFail=True, maxTries=3, waitBetweenTries=3000)

    f.connect("Webhook", "Verify Signature")
    f.connect("Verify Signature", "Signature Valid?")
    f.connect("Signature Valid?", "Respond 202", 0)
    f.connect("Signature Valid?", "Respond 401", 1)
    f.chain("Respond 202", "Start Run", "Find Uncached Criteria", "LLM Normalise Criteria",
            "Save LLM Cache", "Plan Batches", "Loop Batches")
    f.connect("Loop Batches", "Finalize", 0)          # done
    f.connect("Loop Batches", "SQL Pre-filter + Top-K", 1)    # each batch
    f.chain("SQL Pre-filter + Top-K", "Format Matches", "Insert Matches", "Loop Batches")
    f.chain("Finalize", "Trigger Notifications")
    f.write()


# --------------------------------------------------------------------------- C: notification
FETCH_PENDING = """
WITH pending AS (
  SELECT m.id AS match_id, m.product_id, m.score, u.email, u.name
  FROM matches m JOIN users u ON u.user_id = m.user_id
  WHERE m.upload_job_id = $1::uuid AND m.notified_at IS NULL
), best AS (   -- one row per (recipient, product): users sharing an email are merged
  SELECT DISTINCT ON (email, product_id) email, name, product_id, score
  FROM pending ORDER BY email, product_id, score DESC
), ids AS (
  SELECT email, array_agg(match_id) AS match_ids FROM pending GROUP BY email
)
SELECT b.email, min(b.name) AS name, ids.match_ids,
       jsonb_agg(jsonb_build_object('product_name', p.product_name, 'source', p.source,
                 'interest_rate', p.interest_rate, 'min_income', p.min_income,
                 'min_credit_score', p.min_credit_score) ORDER BY b.score DESC) AS products
FROM best b
JOIN product_criteria p ON p.id = b.product_id
JOIN ids ON ids.email = b.email
GROUP BY b.email, ids.match_ids
ORDER BY b.email
LIMIT $2::int
""".strip()

MARK_SENT = """
UPDATE matches SET notified_at = now()
WHERE id IN (SELECT jsonb_array_elements_text($1::jsonb)::bigint) AND notified_at IS NULL
RETURNING id
""".strip()

SUMMARY = """
SELECT count(*) FILTER (WHERE notified_at IS NOT NULL) AS notified_matches,
       count(*) FILTER (WHERE notified_at IS NULL) AS pending_matches
FROM matches WHERE upload_job_id = $1::uuid
""".strip()


def build_notify():
    f = Flow("wfC_notify", "C - User Notification", error_workflow="wfE_error")
    f.add("Webhook", "n8n-nodes-base.webhook", 2,
          {"httpMethod": "POST", "path": "notify-users", "responseMode": "onReceived", "options": {}},
          webhookId="notify-users-wh")
    code_node(f, "Config & Safety Checks", "c_config.js")
    pg(f, "Fetch Pending Matches", FETCH_PENDING, "={{ [$json.upload_job_id, $json.limit] }}", always=True)
    code_node(f, "Build Emails", "c_build_email.js")
    f.add("Loop Emails", "n8n-nodes-base.splitInBatches", 3, {"batchSize": 1, "options": {}})
    f.add("Anything To Send?", "n8n-nodes-base.if", 2.2, {"conditions": {
        "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
        "conditions": [{"id": "empty", "leftValue": "={{ $json.empty === true }}", "rightValue": "",
                        "operator": {"type": "boolean", "operation": "false", "singleValue": True}}],
        "combinator": "and"}, "options": {}})
    f.add("Use SES?", "n8n-nodes-base.if", 2.2, {"conditions": {
        "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
        "conditions": [{"id": "ses", "leftValue": "={{ $json.transport }}", "rightValue": "ses",
                        "operator": {"type": "string", "operation": "equals"}}],
        "combinator": "and"}, "options": {}})
    f.add("Send via AWS SES", "n8n-nodes-base.awsSes", 1, {
        "resource": "email", "operation": "send", "fromEmail": "={{ $json.from }}",
        "toAddresses": "={{ [$json.to] }}", "subject": "={{ $json.subject }}",
        "body": "={{ $json.html }}", "isBodyHtml": True}, AWS, onError="continueRegularOutput")
    f.add("SES Throttle (1/s)", "n8n-nodes-base.wait", 1.1, {"resume": "timeInterval", "amount": 1, "unit": "seconds"})
    f.add("Send via SMTP", "n8n-nodes-base.emailSend", 2.1, {
        "fromEmail": "={{ $json.from }}", "toEmail": "={{ $json.to }}", "subject": "={{ $json.subject }}",
        "emailFormat": "html", "html": "={{ $json.html }}", "options": {}}, SMTP, onError="continueRegularOutput")
    f.add("Sent OK?", "n8n-nodes-base.if", 2.2, {"conditions": {
        "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
        "conditions": [{"id": "ok", "leftValue": "={{ !!$json.error }}", "rightValue": "",
                        "operator": {"type": "boolean", "operation": "false", "singleValue": True}}],
        "combinator": "and"}, "options": {}})
    pg(f, "Mark Notified", MARK_SENT, "={{ [JSON.stringify($('Build Emails').item.json.match_ids)] }}")
    pg(f, "Summary", SUMMARY, "={{ [$('Config & Safety Checks').first().json.upload_job_id] }}", once=True)

    f.chain("Webhook", "Config & Safety Checks", "Fetch Pending Matches", "Build Emails", "Loop Emails")
    f.connect("Loop Emails", "Summary", 0)
    f.connect("Loop Emails", "Anything To Send?", 1)
    f.connect("Anything To Send?", "Use SES?", 0)
    f.connect("Anything To Send?", "Loop Emails", 1)
    f.connect("Use SES?", "Send via AWS SES", 0)
    f.connect("Use SES?", "Send via SMTP", 1)
    f.chain("Send via AWS SES", "SES Throttle (1/s)", "Sent OK?")
    f.connect("Send via SMTP", "Sent OK?")
    f.connect("Sent OK?", "Mark Notified", 0)
    f.connect("Sent OK?", "Loop Emails", 1)   # failed: stays un-notified, retried on next run
    f.connect("Mark Notified", "Loop Emails")
    f.write()


# --------------------------------------------------------------------------- A: discovery
UPSERT_PRODUCTS = """
WITH ins AS (
  INSERT INTO loan_products (source, product_name, interest_rate, min_income, min_credit_score,
      max_credit_score, min_age, max_age, allowed_employment, criteria_text, needs_review, active, last_seen_at)
  SELECT $1, r.product_name, r.interest_rate, r.min_income, r.min_credit_score, r.max_credit_score,
         r.min_age::int, r.max_age::int,
         CASE WHEN jsonb_typeof(r.allowed_employment) = 'array'
              THEN ARRAY(SELECT jsonb_array_elements_text(r.allowed_employment)) END,
         r.criteria_text,
         (r.min_income IS NULL AND r.min_credit_score IS NULL AND r.criteria_text IS NOT NULL),
         true, now()
  FROM jsonb_to_recordset($2::jsonb) AS r(product_name text, interest_rate numeric, min_income numeric,
       min_credit_score int, max_credit_score int, min_age numeric, max_age numeric,
       allowed_employment jsonb, criteria_text text)
  ON CONFLICT (source, product_name) DO UPDATE SET
    interest_rate = EXCLUDED.interest_rate, min_income = EXCLUDED.min_income,
    min_credit_score = EXCLUDED.min_credit_score, max_credit_score = EXCLUDED.max_credit_score,
    min_age = EXCLUDED.min_age, max_age = EXCLUDED.max_age, allowed_employment = EXCLUDED.allowed_employment,
    criteria_text = EXCLUDED.criteria_text, needs_review = EXCLUDED.needs_review,
    active = true, last_seen_at = now()
  RETURNING 1
), retired AS (   -- products that vanished from the page; only when this crawl found something
  UPDATE loan_products SET active = false
  WHERE source = $1 AND active AND last_seen_at < now() AND (SELECT count(*) FROM ins) > 0
  RETURNING 1
), state AS (     -- remember the page hash only after a successful extraction
  INSERT INTO crawl_state (source, content_hash, last_crawled_at, last_status)
  SELECT $1, $3, now(), 'ok' WHERE (SELECT count(*) FROM ins) > 0
  ON CONFLICT (source) DO UPDATE SET content_hash = EXCLUDED.content_hash,
    last_crawled_at = now(), last_status = 'ok'
), failed AS (    -- keep the old hash (so the next run retries) but record why this run stored nothing
  INSERT INTO crawl_state (source, content_hash, last_crawled_at, last_status)
  SELECT $1, NULL, now(), left('error: ' || $4::text, 500)
  WHERE (SELECT count(*) FROM ins) = 0 AND NULLIF($4::text, '') IS NOT NULL
  ON CONFLICT (source) DO UPDATE SET last_crawled_at = now(), last_status = EXCLUDED.last_status
)
SELECT $1 AS source, (SELECT count(*) FROM ins) AS upserted, (SELECT count(*) FROM retired) AS retired
""".strip()

def build_discovery():
    f = Flow("wfA_discovery", "A - Loan Product Discovery", error_workflow="wfE_error")
    f.add("Daily 02:00", "n8n-nodes-base.scheduleTrigger", 1.2,
          {"rule": {"interval": [{"field": "days", "daysInterval": 1, "triggerAtHour": 2}]}})
    f.add("Run Now (manual)", "n8n-nodes-base.manualTrigger", 1)
    pg(f, "Load Crawl State", "SELECT source, content_hash FROM crawl_state", always=True)
    code_node(f, "Sources", "a_sources.js")
    f.add("Fetch Page", "n8n-nodes-base.httpRequest", 4.2, {
        "url": "={{ $json.url }}", "sendHeaders": True, "headerParameters": {"parameters": [
            {"name": "User-Agent", "value": "Mozilla/5.0 (compatible; LoanEligibilityBot/1.0)"}]},
        "options": {"timeout": 30000, "response": {"response": {"responseFormat": "text"}}}},
        retryOnFail=True, maxTries=3, waitBetweenTries=3000, onError="continueRegularOutput")
    code_node(f, "Extract Text", "a_extract_text.js")
    f.add("Changed & Readable?", "n8n-nodes-base.if", 2.2, {"conditions": {
        "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
        "conditions": [
            {"id": "ok", "leftValue": "={{ $json.ok }}", "rightValue": "",
             "operator": {"type": "boolean", "operation": "true", "singleValue": True}},
            {"id": "ch", "leftValue": "={{ $json.changed }}", "rightValue": "",
             "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
        "combinator": "and"}, "options": {}})
    code_node(f, "LLM Extract Products", "a_extract_products.js", mode="runOnceForEachItem")
    pg(f, "Upsert Products", UPSERT_PRODUCTS,
       "={{ [$json.source, JSON.stringify($json.products), $json.content_hash, $json.error || ''] }}")
    code_node(f, "Report Extraction Errors", "a_report.js")

    f.connect("Daily 02:00", "Load Crawl State")
    f.connect("Run Now (manual)", "Load Crawl State")
    f.chain("Load Crawl State", "Sources", "Fetch Page", "Extract Text", "Changed & Readable?")
    f.connect("Changed & Readable?", "LLM Extract Products", 0)
    f.chain("LLM Extract Products", "Upsert Products", "Report Extraction Errors")
    f.write()


if __name__ == "__main__":
    build_error()
    build_matching()
    build_notify()
    build_discovery()
    print("workflows written to", OUT)
