"""Optimisation report for one matching run (default: the latest) from funnel_metrics.

usage: python scripts/benchmark.py [job_id]

Also times the stage-1 SQL join over ALL of that job's users in a single statement, to show how cheap the
set-based filter is next to calling an LLM once per user-product pair.
"""
import sys
import time

import _env

_env.load()

from app import db  # noqa: E402

conn = db.get_conn()
job = sys.argv[1] if len(sys.argv) > 1 else None
row = conn.execute(
    """SELECT upload_job_id, users_count, products_count, pairs_naive, pairs_after_sql, matches_rules,
              llm_products, llm_calls, extract(epoch FROM finished_at - started_at)
       FROM funnel_metrics WHERE (%(j)s::uuid IS NULL OR upload_job_id = %(j)s::uuid)
       ORDER BY started_at DESC LIMIT 1""", {"j": job}).fetchone()
if not row:
    sys.exit("no completed matching run found")
job_id, users, products, naive, after_sql, kept, llm_products, llm_calls, seconds = row

t0 = time.perf_counter()
sql_pairs = conn.execute(
    """SELECT count(*) FROM users b JOIN product_criteria p
       ON b.monthly_income >= COALESCE(p.min_income, 0) AND b.credit_score >= COALESCE(p.min_credit_score, 0)
      AND b.credit_score <= COALESCE(p.max_credit_score, 1000) AND b.age >= COALESCE(p.min_age, 0)
      AND b.age <= COALESCE(p.max_age, 200)
      AND (p.allowed_employment IS NULL OR b.employment_status = ANY (p.allowed_employment))
       WHERE b.upload_job_id = %s""", (job_id,)).fetchone()[0]
sql_ms = (time.perf_counter() - t0) * 1000

pct = lambda a, b: f"{100 * (1 - a / b):.1f}%" if b else "n/a"
print(f"job {job_id}: {users:,} users x {products} products")
print(f"{'stage':<46}{'pairs / calls':>16}{'saved':>9}")
print(f"{'naive: one LLM call per user-product pair':<46}{naive:>16,}{'':>9}")
print(f"{'stage 1: SQL hard-criteria filter':<46}{after_sql:>16,}{pct(after_sql, naive):>9}")
print(f"{'stage 2: top-K per user (written to matches)':<46}{kept:>16,}{pct(kept, naive):>9}")
print(f"{'stage 3: actual LLM calls (' + str(llm_products) + ' free-text products, cached)':<46}{llm_calls:>16,}{pct(llm_calls, naive):>9}")
print(f"\nwhole matching run: {seconds:.1f}s (n8n); the stage-1 join alone over all users: {sql_ms:.0f} ms ({sql_pairs:,} pairs)")
