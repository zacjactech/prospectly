"""Merge fuzzy-scan (M1) and retention sweep (M2) regression tests.

All DB-touching tests use an isolated temp DB so they can never pollute the
real `data/prospectly.db`. No network is touched.
"""

from __future__ import annotations

import os
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import Prospect, Tag
from app.services import merge_service
from app.services.merge_service import (
    MAX_CANDIDATE_PAIRS,
    _candidate_pairs,
    find_fuzzy_duplicates,
)
from app.services.prospect import _normalize_name
from app.services.retention import (
    MAX_RETENTION_MONTHS,
    MIN_RETENTION_MONTHS,
    clear_retention_flag,
    sweep_inactive_prospects,
)


def _make_db() -> tuple[Session, str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    # The production schema carries partial UNIQUE indexes on phone, email and
    # website_url. Structured-key tests need live rows sharing those values,
    # which those indexes would reject, so drop them on this throwaway DB only.
    with engine.begin() as conn:
        for index_name in (
            "uq_prospects_phone",
            "uq_prospects_email",
            "uq_prospects_domain",
        ):
            conn.execute(text(f"DROP INDEX {index_name}"))
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal(), path


def _add_prospect(
    db: Session,
    name: str,
    *,
    phone: str | None = None,
    email: str | None = None,
    website_url: str | None = None,
    city: str | None = None,
    updated_at: datetime | None = None,
    do_not_contact: bool = False,
    archived_at: datetime | None = None,
    tag: Tag | None = None,
) -> Prospect:
    prospect = Prospect(
        business_name=name,
        normalized_name=_normalize_name(name),
        phone_e164=phone,
        email=email,
        website_url=website_url,
        location_city=city,
        do_not_contact=do_not_contact,
        archived_at=archived_at,
    )
    if updated_at is not None:
        prospect.updated_at = updated_at
    if tag is not None:
        prospect.tags.append(tag)
    db.add(prospect)
    db.flush()
    return prospect


# ---------------------------------------------------------------------------
# M1 — fuzzy duplicate scan: prefiltered candidate pairs
# ---------------------------------------------------------------------------


def test_structured_keys_bucket_candidates() -> None:
    """Phone/email/domain buckets pair rows even when names are unrelated."""
    rows = [
        Prospect(id=1, business_name="Alpha", normalized_name="alpha"),
        Prospect(id=2, business_name="Zeta", normalized_name="zeta", phone_e164="+441111"),
        Prospect(id=3, business_name="Kappa", normalized_name="kappa", phone_e164="+441111"),
        Prospect(id=4, business_name="Omega", normalized_name="omega", email="x@y.com"),
        Prospect(id=5, business_name="Sigma", normalized_name="sigma", email="X@Y.com"),
        Prospect(
            id=6,
            business_name="Delta",
            normalized_name="delta",
            website_url="https://www.acme.com/a",
        ),
        Prospect(
            id=7,
            business_name="Theta",
            normalized_name="theta",
            website_url="http://acme.com/b",
        ),
    ]

    assert set(_candidate_pairs(rows)) == {(2, 3), (4, 5), (6, 7)}


def test_unrelated_pairs_are_excluded() -> None:
    rows = [
        Prospect(id=1, business_name="Alpha Cafe", normalized_name="alpha cafe"),
        Prospect(id=2, business_name="Beta Bakery", normalized_name="beta bakery"),
        Prospect(id=3, business_name="Gamma Garage", normalized_name="gamma garage"),
    ]
    assert _candidate_pairs(rows) == []


def test_candidate_pairs_are_capped() -> None:
    rows = [
        Prospect(id=i, business_name=f"Shop {i}", normalized_name=f"shop {i}") for i in range(1, 6)
    ]
    # One shared "shop" token bucket -> C(5,2) = 10 pairs, capped to 3.
    assert _candidate_pairs(rows, max_pairs=3) == [(1, 2), (1, 3), (1, 4)]


def test_oversized_bucket_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(merge_service, "MAX_BUCKET_SIZE", 2)
    rows = [
        Prospect(id=i, business_name=f"Shop {i}", normalized_name=f"shop {i}") for i in range(1, 4)
    ]
    # All three share the generic token "shop"; a >2 row bucket is skipped.
    assert _candidate_pairs(rows) == []


def test_finds_normalized_name_variants() -> None:
    """Punctuation/case/suffix variants normalize to close names and still match."""
    db, path = _make_db()
    try:
        first = _add_prospect(db, "Acme Widgets")
        second = _add_prospect(db, "Acme Widgets Ltd!")

        pairs = find_fuzzy_duplicates(db)

        assert len(pairs) == 1
        pair = pairs[0]
        assert pair["target"].id == first.id
        assert pair["source"].id == second.id
        assert pair["score"] >= 0.85
        assert pair["reason"] == "name similarity"
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_reports_structured_match_reasons() -> None:
    db, path = _make_db()
    try:
        _add_prospect(
            db,
            "Acme Widgets",
            phone="+441234567890",
            email="hello@acme.test",
            website_url="https://acme.test/",
            city="Bristol",
        )
        _add_prospect(
            db,
            "Acme Widget",
            phone="+441234567890",
            email="HELLO@acme.test",
            website_url="https://acme.test/",
            city="Bristol",
        )

        pairs = find_fuzzy_duplicates(db)

        assert len(pairs) == 1
        reason = pairs[0]["reason"]
        assert "same phone" in reason
        assert "same email" in reason
        assert "same website" in reason
        assert "same city (Bristol)" in reason
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_limit_keeps_lowest_id_pairs() -> None:
    db, path = _make_db()
    try:
        first = _add_prospect(db, "Acme Widgets")
        second = _add_prospect(db, "Acme Widgets Ltd")
        _add_prospect(db, "Acme Widget")

        pairs = find_fuzzy_duplicates(db, limit=1)

        assert len(pairs) == 1
        assert pairs[0]["target"].id == first.id
        assert pairs[0]["source"].id == second.id
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_min_score_filters_pairs() -> None:
    db, path = _make_db()
    try:
        _add_prospect(db, "Acme Widgets")
        _add_prospect(db, "Acme Widgets Ltd")

        assert find_fuzzy_duplicates(db, min_score=0.99) == []
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_5000_prospects_scan_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """5,000 live prospects must not produce an O(n^2) scoring scan."""
    db, path = _make_db()
    try:
        db.add_all(
            Prospect(
                business_name=f"Prospect {i:05d}",
                normalized_name=f"prospect {i:05d}",
            )
            for i in range(5000)
        )
        db.flush()

        calls = 0
        real_score = merge_service._fuzzy_score

        def counting_score(a: str, b: str) -> float:
            nonlocal calls
            calls += 1
            return real_score(a, b)

        monkeypatch.setattr(merge_service, "_fuzzy_score", counting_score)

        start = time.perf_counter()
        pairs = find_fuzzy_duplicates(db)
        elapsed = time.perf_counter() - start

        assert pairs == []
        assert calls <= MAX_CANDIDATE_PAIRS
        assert elapsed < 5.0
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


# ---------------------------------------------------------------------------
# M2 — retention sweep: month clamping, truthful skipped count, clear flag
# ---------------------------------------------------------------------------


def _days_ago(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


@pytest.mark.parametrize("months", [0, -5])
def test_sweep_clamps_low_months(months: int) -> None:
    db, path = _make_db()
    try:
        old = _add_prospect(db, "Old Co", updated_at=_days_ago(60))
        _add_prospect(db, "Recent Co", updated_at=_days_ago(1))

        result = sweep_inactive_prospects(db, months=months)

        assert result["months"] == MIN_RETENTION_MONTHS
        cutoff = datetime.fromisoformat(result["cutoff"])
        assert timedelta(days=29) < datetime.now(UTC) - cutoff < timedelta(days=31)
        assert result["flagged_ids"] == [old.id]
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_sweep_clamps_high_months() -> None:
    db, path = _make_db()
    try:
        _add_prospect(db, "Year Old Co", updated_at=_days_ago(400))
        ancient = _add_prospect(db, "Ancient Co", updated_at=_days_ago(4000))

        result = sweep_inactive_prospects(db, months=10_000)

        assert result["months"] == MAX_RETENTION_MONTHS
        assert result["flagged_ids"] == [ancient.id]
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_sweep_skips_dnc_and_already_flagged() -> None:
    db, path = _make_db()
    try:
        retention_tag = Tag(name="retention-review")
        db.add(retention_tag)
        db.flush()

        _add_prospect(db, "Do Not Contact Co", updated_at=_days_ago(400), do_not_contact=True)
        flagged_before = _add_prospect(
            db, "Already Flagged Co", updated_at=_days_ago(400), tag=retention_tag
        )
        newly = _add_prospect(db, "Neglected Co", updated_at=_days_ago(400))

        result = sweep_inactive_prospects(db, months=12)

        assert result["flagged"] == 1
        assert result["flagged_ids"] == [newly.id]
        assert result["already_flagged"] == 1
        # One do_not_contact row + one already-flagged row.
        assert result["skipped"] == 2
        assert flagged_before.id not in result["flagged_ids"]
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_sweep_flags_only_genuinely_inactive_rows() -> None:
    db, path = _make_db()
    try:
        inactive = _add_prospect(db, "Inactive Co", updated_at=_days_ago(400))
        _add_prospect(db, "Recent Co", updated_at=_days_ago(10))
        _add_prospect(db, "Archived Co", updated_at=_days_ago(400), archived_at=_days_ago(200))
        _add_prospect(db, "Dnc Co", updated_at=_days_ago(400), do_not_contact=True)

        result = sweep_inactive_prospects(db, months=12)

        assert result["flagged_ids"] == [inactive.id]
        assert result["flagged"] == 1
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_clear_retention_flag_on_unflagged_prospect() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, "Not Flagged Co")

        assert clear_retention_flag(db, prospect.id) is False
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_clear_retention_flag_removes_tag() -> None:
    db, path = _make_db()
    try:
        retention_tag = Tag(name="retention-review")
        db.add(retention_tag)
        db.flush()
        prospect = _add_prospect(db, "Flagged Co", tag=retention_tag)

        assert clear_retention_flag(db, prospect.id) is True
        assert retention_tag not in prospect.tags
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)
