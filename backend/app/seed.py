"""Load seed/loan_products.json into loan_products (idempotent upsert)."""
import json
import pathlib

from app import db

SEED_FILE = pathlib.Path(__file__).resolve().parent.parent / "seed" / "loan_products.json"
_DEFAULTS = {"max_credit_score": None, "min_age": None, "max_age": None, "allowed_employment": None,
             "criteria_text": None, "needs_review": False, "min_income": None, "min_credit_score": None}


def seed() -> int:
    products = json.loads(SEED_FILE.read_text())
    conn = db.get_conn()
    for p in products:
        conn.execute(
            """
            INSERT INTO loan_products (source, product_name, interest_rate, min_income, min_credit_score,
                max_credit_score, min_age, max_age, allowed_employment, criteria_text, needs_review)
            VALUES (%(source)s, %(product_name)s, %(interest_rate)s, %(min_income)s, %(min_credit_score)s,
                %(max_credit_score)s, %(min_age)s, %(max_age)s, %(allowed_employment)s, %(criteria_text)s,
                %(needs_review)s)
            ON CONFLICT (source, product_name) DO UPDATE SET
                interest_rate = EXCLUDED.interest_rate, min_income = EXCLUDED.min_income,
                min_credit_score = EXCLUDED.min_credit_score, max_credit_score = EXCLUDED.max_credit_score,
                min_age = EXCLUDED.min_age, max_age = EXCLUDED.max_age,
                allowed_employment = EXCLUDED.allowed_employment, criteria_text = EXCLUDED.criteria_text,
                needs_review = EXCLUDED.needs_review, active = true, last_seen_at = now()
            """, {**_DEFAULTS, **p})
    conn.commit()
    return len(products)
