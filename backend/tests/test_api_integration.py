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
