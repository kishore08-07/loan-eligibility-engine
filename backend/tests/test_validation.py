import pytest

from app import validation, webhook
from app.validation import RowError, parse_row

GOOD = {"user_id": "u1", "name": "Asha", "email": "Asha@Example.com", "monthly_income": "50000",
        "credit_score": "720", "employment_status": "salaried", "age": "30"}


def test_valid_row_is_normalised():
    row = parse_row(GOOD)
    assert row.email == "asha@example.com"
    assert row.employment_status == "Salaried"
    assert (row.monthly_income, row.credit_score, row.age) == (50000.0, 720, 30)


def test_name_is_optional():
    assert parse_row({**GOOD, "name": ""}).name is None


@pytest.mark.parametrize("field,value", [
    ("user_id", ""), ("email", "nope"), ("monthly_income", "abc"), ("monthly_income", "-5"),
    ("credit_score", "950"), ("credit_score", "700.5"), ("age", "10"), ("employment_status", "Gig"),
])
def test_invalid_rows_are_rejected(field, value):
    with pytest.raises(RowError):
        parse_row({**GOOD, field: value})


def test_missing_columns():
    assert validation.missing_columns(["user_id", "email"]) == [
        "monthly_income", "credit_score", "employment_status", "age"]
    assert validation.missing_columns(list(validation.REQUIRED_COLUMNS)) == []


def test_webhook_signature_is_deterministic_and_secret_dependent():
    a = webhook.sign("s1", "100", b"{}")
    assert a == webhook.sign("s1", "100", b"{}")
    assert a != webhook.sign("s2", "100", b"{}")
    assert a != webhook.sign("s1", "101", b"{}")
