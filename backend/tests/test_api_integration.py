"""HTTP API handlers against real Postgres + moto S3."""
import json

from app.handlers import api


def _event(body=None, path=None, query=None):
    return {"body": json.dumps(body) if body is not None else None,
            "pathParameters": path or {}, "queryStringParameters": query or {}}


def test_health(env):
    assert api.health({})["statusCode"] == 200


def test_create_upload_returns_presigned_post_and_pending_job(env):
    conn, _, _ = env
    resp = api.create_upload(_event({"filename": "users.csv"}))
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 201
    assert body["upload"]["fields"]["key"] == f"uploads/{body['job_id']}.csv"
    assert body["upload"]["fields"]["policy"]  # signed policy carries the size limit
    assert conn.execute("SELECT status FROM upload_jobs").fetchone()[0] == "PENDING"


def test_create_upload_rejects_non_csv(env):
    assert api.create_upload(_event({"filename": "users.xlsx"}))["statusCode"] == 400
    assert api.create_upload({"body": "not json"})["statusCode"] == 400


def test_get_upload_and_matches(env):
    conn, _, _ = env
    job_id = json.loads(api.create_upload(_event({"filename": "u.csv"}))["body"])["job_id"]
    conn.execute("INSERT INTO users VALUES ('u1','A','a@x.com',50000,700,'Salaried',30,%s,now(),now())", (job_id,))
    conn.execute("INSERT INTO loan_products (source, product_name, interest_rate) VALUES ('s','P',10)")
    conn.execute("INSERT INTO matches (upload_job_id, user_id, product_id, score, match_stage) "
                 "SELECT %s, 'u1', id, 55.5, 'rules' FROM loan_products", (job_id,))
    conn.commit()

    got = json.loads(api.get_upload(_event(path={"job_id": job_id}))["body"])
    assert got["matches"] == 1 and got["notified"] == 0 and got["status"] == "PENDING"

    listing = json.loads(api.list_matches(_event(path={"job_id": job_id}, query={"limit": "10"}))["body"])
    assert listing["total"] == 1 and listing["items"][0]["product_name"] == "P"
    assert listing["items"][0]["score"] == 55.5


def test_unknown_and_invalid_job_ids(env):
    assert api.get_upload(_event(path={"job_id": "not-a-uuid"}))["statusCode"] == 400
    assert api.get_upload(_event(path={"job_id": "0" * 8 + "-0000-0000-0000-" + "0" * 12}))["statusCode"] == 404


def test_api_key_is_enforced_when_configured(env, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "API_KEY", "s3cret")
    assert api.list_uploads({})["statusCode"] == 401
    assert api.list_uploads({"headers": {"x-api-key": "wrong"}})["statusCode"] == 401
    assert api.list_uploads({"headers": {"x-api-key": "s3cret"}})["statusCode"] == 200
    assert api.health({})["statusCode"] == 200  # health stays public for uptime checks


def test_list_uploads_newest_first_and_match_pagination(env):
    conn, _, _ = env
    first = json.loads(api.create_upload(_event({"filename": "a.csv"}))["body"])["job_id"]
    second = json.loads(api.create_upload(_event({"filename": "b.csv"}))["body"])["job_id"]
    items = json.loads(api.list_uploads({})["body"])["items"]
    assert [i["id"] for i in items] == [second, first]

    conn.execute("INSERT INTO loan_products (source, product_name) SELECT 's', 'P' || g FROM generate_series(1, 3) g")
    conn.execute("INSERT INTO users VALUES ('u1','A','a@x.com',50000,700,'Salaried',30,%s,now(),now())", (first,))
    conn.execute("INSERT INTO matches (upload_job_id, user_id, product_id, score, match_stage) "
                 "SELECT %s, 'u1', id, 50, 'rules' FROM loan_products", (first,))
    conn.commit()
    page = json.loads(api.list_matches(_event(path={"job_id": first}, query={"limit": "2", "offset": "2"}))["body"])
    assert page["total"] == 3 and len(page["items"]) == 1
    clamped = json.loads(api.list_matches(_event(path={"job_id": first}, query={"limit": "9999"}))["body"])
    assert clamped["limit"] == 200


def test_reprocess_duplicate_creates_a_forced_job_and_copies_the_file(env):
    conn, s3, _ = env
    from tests.conftest import BUCKET
    created = json.loads(api.create_upload(_event({"filename": "u.csv"}))["body"])
    job_id = created["job_id"]
    s3.put_object(Bucket=BUCKET, Key=f"uploads/{job_id}.csv", Body=b"user_id\n")

    not_dup = api.reprocess_upload(_event(path={"job_id": job_id}))
    assert not_dup["statusCode"] == 409  # only DUPLICATE jobs qualify

    conn.execute("UPDATE upload_jobs SET status = 'DUPLICATE' WHERE id = %s", (job_id,))
    conn.commit()
    resp = api.reprocess_upload(_event(path={"job_id": job_id}))
    new_id = json.loads(resp["body"])["job_id"]
    assert resp["statusCode"] == 201 and new_id != job_id
    assert conn.execute("SELECT allow_duplicate, status FROM upload_jobs WHERE id = %s", (new_id,)).fetchone() == (True, "PENDING")
    assert s3.get_object(Bucket=BUCKET, Key=f"uploads/{new_id}.csv")["Body"].read() == b"user_id\n"

    assert api.reprocess_upload(_event(path={"job_id": "bad"}))["statusCode"] == 400
    assert api.reprocess_upload(_event(path={"job_id": "0" * 8 + "-0000-0000-0000-" + "0" * 12}))["statusCode"] == 404
