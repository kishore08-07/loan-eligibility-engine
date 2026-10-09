"""The n8n side: generated workflow JSON is current, and its core SQL behaves on a real Postgres."""
import importlib.util
import json
import pathlib
import re

N8N = pathlib.Path(__file__).resolve().parents[2] / "n8n"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_workflows", N8N / "build_workflows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_workflows_match_their_sources(tmp_path, monkeypatch):
    """n8n/workflows/*.json are generated; fail if someone edited code/*.js or the SQL without rebuilding."""
    builder = _load_builder()
    monkeypatch.setattr(builder, "OUT", tmp_path)
    for build in (builder.build_error, builder.build_matching, builder.build_notify, builder.build_discovery):
        build()
    for fresh in tmp_path.glob("*.json"):
        committed = json.loads((N8N / "workflows" / fresh.name).read_text())
        assert json.loads(fresh.read_text()) == committed, f"{fresh.name} is stale: run python3 n8n/build_workflows.py"


def _run(conn, sql, params):
    """n8n's Postgres node uses $1..$n placeholders; psycopg wants named ones."""
    named = re.sub(r"\$(\d+)", r"%(p\1)s", sql)
    return conn.execute(named, {f"p{i}": v for i, v in enumerate(params, start=1)})


def _seed_job(conn, products):
    conn.execute("INSERT INTO upload_jobs (id, filename, s3_key, status) "
                 "VALUES ('00000000-0000-0000-0000-000000000001', 'f.csv', 'uploads/x.csv', 'COMPLETED')")
    users = [("rich", 200000, 780, 35, "Salaried"), ("young", 90000, 750, 19, "Salaried"),
             ("poor", 5000, 780, 35, "Salaried"), ("selfemp", 90000, 700, 40, "Self-Employed"),
             ("lowscore", 90000, 500, 40, "Salaried")]
    for uid, income, score, age, emp in users:
        conn.execute("INSERT INTO users VALUES (%s, %s, %s, %s, %s, %s, %s, "
                     "'00000000-0000-0000-0000-000000000001', now(), now())",
                     (uid, uid, f"{uid}@x.com", income, score, emp, age))
    for name, rate, min_income, min_score, min_age, employment in products:
        conn.execute("INSERT INTO loan_products (source, product_name, interest_rate, min_income, min_credit_score, "
                     "min_age, allowed_employment) VALUES ('t', %s, %s, %s, %s, %s, %s)",
                     (name, rate, min_income, min_score, min_age, employment))
    conn.commit()


def _prefilter(conn, top_k):
    params = ["00000000-0000-0000-0000-000000000001", 0, 1000, 0.35, 0.25, 0.4, top_k]
    return _run(conn, _load_builder().PREFILTER, params).fetchall()


def test_prefilter_applies_hard_criteria(env):
    conn, _, _ = env
    _seed_job(conn, [("Basic", 15, 25000, 650, 21, None), ("SalariedOnly", 11, 40000, 700, 21, ["Salaried"])])
    matched = {(r[0], r[1]) for r in _prefilter(conn, 10)}
    names = dict(conn.execute("SELECT id, product_name FROM loan_products").fetchall())
    got = {(u, names[p]) for u, p in matched}
    assert got == {("rich", "Basic"), ("rich", "SalariedOnly"), ("selfemp", "Basic")}
    # young (age 19 < 21), poor (income), lowscore (credit) match nothing; selfemp fails the employment rule


def test_prefilter_top_k_keeps_best_products_and_reports_eligible_total(env):
    conn, _, _ = env
    _seed_job(conn, [("Cheap", 9, 10000, 600, 18, None), ("Pricey", 19, 10000, 600, 18, None),
                     ("Mid", 14, 10000, 600, 18, None)])
    rows = _prefilter(conn, 1)
    names = dict(conn.execute("SELECT id, product_name FROM loan_products").fetchall())
    per_user = {}
    for user, product, *_ in rows:
        per_user.setdefault(user, []).append(names[product])
    assert all(v == ["Cheap"] for v in per_user.values())  # lower rate scores higher
    assert len(per_user) == 3 and {r[-1] for r in rows} == {9}  # eligible_total counts pairs before top-K


def test_upsert_products_records_errors_without_touching_hash(env):
    conn, _, _ = env
    sql = _load_builder().UPSERT_PRODUCTS
    conn.execute("INSERT INTO crawl_state VALUES ('src', 'oldhash', now(), 'ok')")
    conn.commit()
    _run(conn, sql, ["src", "[]", "newhash", "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set"])
    conn.commit()
    assert conn.execute("SELECT content_hash, last_status FROM crawl_state").fetchone() == (
        "oldhash", "error: LLM_PROVIDER=gemini but GEMINI_API_KEY is not set")

    product = json.dumps([{"product_name": "P", "interest_rate": 12, "min_income": 20000, "min_credit_score": 650,
                           "max_credit_score": None, "min_age": 21, "max_age": 60,
                           "allowed_employment": None, "criteria_text": None}])
    _run(conn, sql, ["src", product, "newhash", ""])
    conn.commit()
    assert conn.execute("SELECT content_hash, last_status FROM crawl_state").fetchone() == ("newhash", "ok")
    assert conn.execute("SELECT count(*) FROM loan_products WHERE source = 'src' AND active").fetchone()[0] == 1
