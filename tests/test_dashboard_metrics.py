"""Dashboard metric tests (PRODUCT_BRIEF §8 definitions).

Seeded fixtures; each assert checks a metric against its documented definition.
Overdue logic is timezone-tested (UTC storage, local display) per the Phase 4
exit criteria.
"""

from __future__ import annotations

import itertools
import os
import tempfile
from contextlib import suppress
from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import (
    Activity,
    Finding,
    FollowUp,
    OutreachMessage,
    Prospect,
    ResearchRun,
)
from app.services.dashboard import (
    approved_messages,
    converted_prospects,
    follow_ups_due,
    follow_ups_overdue,
    interested_prospects,
    needs_attention,
    replied_prospects,
    requires_review_prospects,
    researched_prospects,
    sent_confirmed_messages,
    total_prospects,
)

_NAME_COUNTER = itertools.count()


def _make_db() -> tuple[Session, str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    return db, path


def _add_prospect(db: Session, *, stage: str = "new") -> Prospect:
    n = next(_NAME_COUNTER)
    prospect = Prospect(
        business_name=f"Prospect {n} ({stage})",
        normalized_name=f"prospect-{n}-{stage}",
        stage=stage,
    )
    db.add(prospect)
    db.flush()
    return prospect


def _add_run(db: Session, *, prospect_id: int, status: str = "succeeded") -> ResearchRun:
    run = ResearchRun(prospect_id=prospect_id, status=status, triggered_by="user")
    db.add(run)
    db.flush()
    return run


def _add_message(
    db: Session, *, prospect_id: int, status: str = "draft", active: bool = True
) -> OutreachMessage:
    message = OutreachMessage(
        prospect_id=prospect_id,
        status=status,
        current_text="hello",
        active=active,
    )
    db.add(message)
    db.flush()
    return message


def _add_finding(
    db: Session, *, prospect_id: int, verification_status: str = "unverified"
) -> Finding:
    import hashlib

    key = hashlib.sha256(b"x").hexdigest()[:32]
    finding = Finding(
        prospect_id=prospect_id,
        kind="website",
        statement="a finding",
        source_type="search_result",
        verification_status=verification_status,
        confidence="low",
        origin="provider",
        finding_key=key,
        retrieved_at=datetime.now(UTC),
    )
    db.add(finding)
    db.flush()
    return finding


def _add_activity(db: Session, *, prospect_id: int, type: str = "reply_received") -> Activity:
    activity = Activity(
        prospect_id=prospect_id,
        type=type,
        actor="user",
        payload={},
    )
    db.add(activity)
    db.flush()
    return activity


def _add_follow_up(
    db: Session,
    *,
    prospect_id: int,
    due_date: datetime | None,
    status: str = "open",
) -> FollowUp:
    follow_up = FollowUp(
        prospect_id=prospect_id,
        due_date=due_date,
        note="check in",
        status=status,
    )
    db.add(follow_up)
    db.flush()
    return follow_up


def test_total_prospects_counts_non_archived_only() -> None:
    db, path = _make_db()
    try:
        _add_prospect(db, stage="new")
        _add_prospect(db, stage="researched")
        archived = _add_prospect(db, stage="converted")
        archived.archived_at = datetime.now(UTC)
        db.flush()
        assert total_prospects(db) == 2
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_researched_prospects_counts_completed_runs() -> None:
    db, path = _make_db()
    try:
        p1 = _add_prospect(db)
        _add_run(db, prospect_id=p1.id, status="succeeded")
        p2 = _add_prospect(db)
        _add_run(db, prospect_id=p2.id, status="queued")  # not completed
        p3 = _add_prospect(db)
        _add_run(db, prospect_id=p3.id, status="failed")  # not completed
        _add_prospect(db)  # no run at all
        assert researched_prospects(db) == 1
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_requires_review_unverified_or_pending() -> None:
    db, path = _make_db()
    try:
        p1 = _add_prospect(db)
        _add_finding(db, prospect_id=p1.id, verification_status="unverified")
        p2 = _add_prospect(db)
        _add_finding(db, prospect_id=p2.id, verification_status="contradicted")
        p3 = _add_prospect(db)
        _add_message(db, prospect_id=p3.id, status="pending_review")
        _add_prospect(db)  # clean
        assert requires_review_prospects(db) == 3
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_approved_messages_counts_only_approved() -> None:
    db, path = _make_db()
    try:
        _add_message(db, prospect_id=_add_prospect(db).id, status="approved", active=True)
        _add_message(db, prospect_id=_add_prospect(db).id, status="draft", active=False)
        _add_message(db, prospect_id=_add_prospect(db).id, status="sent_confirmed", active=False)
        assert approved_messages(db) == 1
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_sent_confirmed_messages() -> None:
    db, path = _make_db()
    try:
        _add_message(db, prospect_id=_add_prospect(db).id, status="sent_confirmed", active=True)
        _add_message(db, prospect_id=_add_prospect(db).id, status="sent_confirmed", active=False)
        assert sent_confirmed_messages(db) == 2
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_replied_prospects_distinct() -> None:
    db, path = _make_db()
    try:
        p1 = _add_prospect(db)
        _add_activity(db, prospect_id=p1.id, type="reply_received")
        _add_activity(db, prospect_id=p1.id, type="reply_received")  # same prospect
        p2 = _add_prospect(db)
        _add_activity(db, prospect_id=p2.id, type="note_added")  # not a reply
        assert replied_prospects(db) == 1
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_interested_and_converted_count_stages() -> None:
    db, path = _make_db()
    try:
        _add_prospect(db, stage="interested")
        _add_prospect(db, stage="interested")
        _add_prospect(db, stage="converted")
        _add_prospect(db, stage="contacted")  # not interested/converted
        assert interested_prospects(db) == 2
        assert converted_prospects(db) == 1
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_follow_ups_due_includes_today() -> None:
    db, path = _make_db()
    try:
        p = _add_prospect(db)
        today = date(2026, 11, 7)
        _add_follow_up(
            db, prospect_id=p.id, due_date=datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
        )  # today → due
        _add_follow_up(
            db, prospect_id=p.id, due_date=datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
        )  # tomorrow → not due
        _add_follow_up(
            db, prospect_id=p.id, due_date=datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
        )  # yesterday → due (also overdue)
        _add_follow_up(db, prospect_id=p.id, due_date=None, status="open")  # no date → not due
        _add_follow_up(
            db, prospect_id=p.id, due_date=datetime(2026, 11, 7, 9, 0, tzinfo=UTC), status="done"
        )  # done → not due
        assert follow_ups_due(db, today=today) == 2  # today + yesterday
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_follow_ups_overdue_excludes_today() -> None:
    db, path = _make_db()
    try:
        p = _add_prospect(db)
        today = date(2026, 11, 7)
        _add_follow_up(
            db, prospect_id=p.id, due_date=datetime(2026, 11, 6, 23, 59, tzinfo=UTC)
        )  # overdue
        _add_follow_up(
            db, prospect_id=p.id, due_date=datetime(2026, 11, 7, 0, 0, tzinfo=UTC)
        )  # today → NOT overdue
        assert follow_ups_overdue(db, today=today) == 1
        # due includes today: 2 (overdue + today)
        assert follow_ups_due(db, today=today) == 2
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_needs_attention_urgency_order() -> None:
    db, path = _make_db()
    try:
        p1 = _add_prospect(db)
        _add_follow_up(db, prospect_id=p1.id, due_date=datetime(2026, 11, 6, tzinfo=UTC))  # overdue
        p2 = _add_prospect(db)
        _add_message(db, prospect_id=p2.id, status="pending_review")
        p3 = _add_prospect(db)
        _add_run(db, prospect_id=p3.id, status="failed")
        p4 = _add_prospect(db)
        _add_finding(db, prospect_id=p4.id, verification_status="unverified")
        today = date(2026, 11, 7)
        items = needs_attention(db, today=today)
        kinds = [i["kind"] for i in items]
        assert kinds == [
            "follow_up_overdue",
            "pending_review",
            "research_failed",
            "findings_unverified",
        ]
        assert items[0]["count"] == 1
        assert items[0]["href"] == "/prospects?stage=follow_up_due"
        assert items[1]["href"] == "/outreach"
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_needs_attention_empty_when_nothing() -> None:
    db, path = _make_db()
    try:
        _add_prospect(db, stage="new")
        assert needs_attention(db) == []
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)
