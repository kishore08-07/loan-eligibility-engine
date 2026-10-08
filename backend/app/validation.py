"""Row-level validation/normalisation for the users CSV. Pure functions, no I/O."""
import re
from dataclasses import dataclass

REQUIRED_COLUMNS = ("user_id", "email", "monthly_income", "credit_score", "employment_status", "age")
OPTIONAL_COLUMNS = ("name",)
EMPLOYMENT_STATUSES = {"salaried": "Salaried", "self-employed": "Self-Employed", "business": "Business"}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class UserRow:
    user_id: str
    name: str | None
    email: str
    monthly_income: float
    credit_score: int
    employment_status: str
    age: int


class RowError(ValueError):
    pass


def missing_columns(header: list[str] | None) -> list[str]:
    present = {h.strip() for h in (header or [])}
    return [c for c in REQUIRED_COLUMNS if c not in present]


def _int(value: str, field: str, lo: int, hi: int) -> int:
    try:
        number = float(value)
    except ValueError:
        raise RowError(f"{field} is not a number") from None
    if number != int(number):
        raise RowError(f"{field} must be a whole number")
    if not lo <= number <= hi:
        raise RowError(f"{field} must be between {lo} and {hi}")
    return int(number)


def parse_row(raw: dict[str, str]) -> UserRow:
    """Return a clean UserRow or raise RowError with a human-readable reason."""
    row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
    user_id = row.get("user_id", "")
    if not user_id or len(user_id) > 64:
        raise RowError("user_id is empty or longer than 64 chars")
    email = row.get("email", "").lower()
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise RowError("email is invalid")
    try:
        income = float(row.get("monthly_income", ""))
    except ValueError:
        raise RowError("monthly_income is not a number") from None
    if not 0 < income < 1e9:
        raise RowError("monthly_income must be positive")
    status = EMPLOYMENT_STATUSES.get(row.get("employment_status", "").lower())
    if status is None:
        raise RowError(f"employment_status must be one of {sorted(EMPLOYMENT_STATUSES.values())}")
    return UserRow(
        user_id=user_id,
        name=row.get("name") or None,
        email=email,
        monthly_income=income,
        credit_score=_int(row.get("credit_score", ""), "credit_score", 300, 900),
        employment_status=status,
        age=_int(row.get("age", ""), "age", 18, 100),
    )
