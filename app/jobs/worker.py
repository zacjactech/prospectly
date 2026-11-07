"""Job queue worker (SYSTEM_ARCHITECTURE §6).

Claims jobs from the SQLite ``jobs`` table, processes them, and handles
retries with exponential backoff. Stale ``running`` jobs (>10 min) are
reclaimed on startup. Runs as a synchronous loop suitable for a background
thread or a standalone process.

No network I/O happens inside a DB transaction; provider calls are made
after the job is claimed and the transaction is committed.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Job, ResearchRun, Source
from app.providers.factory import get_discovery_provider, get_search_provider
from app.services import research as research_service
from app.services.activity import log_activity
from app.services.prospect import check_duplicates, create_prospect

# How long a job can stay "running" before it's considered stale and
# reclaimed (SYSTEM_ARCHITECTURE §6).
STALE_JOB_MINUTES = 10
# Base delay between worker loop iterations (seconds).
POLL_INTERVAL_SECONDS = 2


def _now() -> datetime:
    return datetime.now(UTC)


def claim_job(db: Session) -> Job | None:
    """Claim the next queued job using BEGIN IMMEDIATE for SQLite locking.

    Returns the claimed job, or None when no job is available.
    """
    # Reclaim stale running jobs first.
    stale_cutoff = _now() - timedelta(minutes=STALE_JOB_MINUTES)
    stale = db.query(Job).filter(Job.status == "running", Job.updated_at < stale_cutoff).all()
    for job in stale:
        job.status = "queued"
        job.locked_at = None
        job.last_error = "Reclaimed after stale running state"
        db.flush()
        log_activity(
            db,
            prospect_id=0,  # system-level, no specific prospect
            type="job_reclaimed",
            actor="system",
            payload={"job_id": job.id, "type": job.type},
        )

    db.flush()

    # Claim the next queued job.
    next_job: Job | None = (
        db.query(Job)
        .filter(Job.status == "queued", Job.run_after <= _now())
        .order_by(Job.created_at.asc())
        .first()
    )
    if next_job is None:
        return None

    next_job.status = "running"
    next_job.locked_at = _now()
    next_job.attempts = (next_job.attempts or 0) + 1
    db.flush()
    return next_job
    job.locked_at = _now()
    job.attempts = (job.attempts or 0) + 1
    db.flush()
    return job


def _run_research_job(db: Session, job: Job) -> None:
    """Process a research job: run the research pipeline for one prospect."""
    payload = job.payload or {}
    prospect_id = payload.get("prospect_id")
    run_id = payload.get("research_run_id")

    if prospect_id is None or run_id is None:
        job.status = "failed"
        job.last_error = "Missing prospect_id or research_run_id in payload"
        db.flush()
        return

    run = db.get(ResearchRun, run_id)
    if run is None:
        job.status = "failed"
        job.last_error = f"Research run {run_id} not found"
        db.flush()
        return

    try:
        research_service.run_research(db, prospect_id=prospect_id, research_run=run)
        job.status = "succeeded"
    except Exception as exc:  # pragma: no cover - defensive
        job.status = "failed"
        job.last_error = str(exc)
    db.flush()


def _run_discover_job(db: Session, job: Job) -> None:
    """Process a discovery job: run profile-driven discovery."""
    payload = job.payload or {}
    profile_id = payload.get("profile_id")

    if profile_id is None:
        job.status = "failed"
        job.last_error = "Missing profile_id in payload"
        db.flush()
        return

    from app.models import DiscoveryJob, Profile

    dj = db.get(DiscoveryJob, payload.get("discovery_job_id"))
    profile = db.get(Profile, profile_id)
    if profile is None or dj is None:
        job.status = "failed"
        job.last_error = "Profile or discovery job not found"
        db.flush()
        return

    discovery_provider = get_discovery_provider()
    search_provider = get_search_provider()

    dj.status = "running"
    dj.started_at = _now()
    db.flush()

    candidates_found = 0
    prospects_created = 0
    errors: list[str] = []

    # Step 1: search for candidates.
    if search_provider:
        try:
            industries = profile.target_industries or []
            locations = profile.target_locations or []
            queries = []
            for ind in industries:
                for loc in locations:
                    queries.append(f"{ind} {loc}")
            if not queries:
                queries = ["business " + (locations[0] if locations else "")]

            for q in queries[:5]:  # cap queries
                try:
                    results = asyncio.run(search_provider.search(q, max_results=5))
                    candidates_found += len(results)
                    for r in results:
                        # Create a candidate prospect (stage=new, awaiting review).
                        dupe = check_duplicates(
                            db,
                            business_name=r.get("title", ""),
                            website_url=r.get("url"),
                            email=None,
                            phone_e164=None,
                            location_city=None,
                        )
                        if dupe["exact"] is not None:
                            continue  # skip exact duplicates

                        source = db.query(Source).filter(Source.type == "web_search").first()
                        if source is None:
                            source = Source(
                                type="web_search",
                                label="Web search discovery",
                                detail={"query": q},
                            )
                            db.add(source)
                            db.flush()

                        p, err = create_prospect(
                            db,
                            business_name=r.get("title", ""),
                            source=source,
                            category=None,
                            location_city=None,
                            address=None,
                            phone_e164=None,
                            phone_raw=None,
                            email=None,
                            website_url=r.get("url"),
                            social_links=None,
                            actor="system",
                            skip_dupe_check=True,
                        )
                        if p is not None:
                            prospects_created += 1
                            log_activity(
                                db,
                                prospect_id=p.id,
                                type="discovered",
                                actor="system",
                                payload={"query": q, "source": "web_search"},
                            )
                except Exception as exc:  # pragma: no cover
                    errors.append(f"search '{q}': {exc!s}")
        except Exception as exc:  # pragma: no cover
            errors.append(f"search: {exc!s}")

    # Step 2: OSM enrichment.
    if discovery_provider and not errors:
        try:
            candidates = asyncio.run(
                discovery_provider.discover(
                    industries=profile.target_industries or [],
                    locations=profile.target_locations or [],
                    max_results=10,
                )
            )
            candidates_found += len(candidates)
            for c in candidates:
                dupe = check_duplicates(
                    db,
                    business_name=c.get("name", ""),
                    website_url=c.get("website"),
                    email=None,
                    phone_e164=None,
                    location_city=None,
                )
                if dupe["exact"] is not None:
                    continue
                p, err = create_prospect(
                    db,
                    business_name=c.get("name", ""),
                    source=None,
                    category=c.get("category"),
                    location_city=None,
                    address=c.get("address"),
                    phone_e164=None,
                    phone_raw=c.get("phone"),
                    email=None,
                    website_url=c.get("website"),
                    social_links=None,
                    actor="system",
                    skip_dupe_check=True,
                )
                if p is not None:
                    prospects_created += 1
                    log_activity(
                        db,
                        prospect_id=p.id,
                        type="discovered",
                        actor="system",
                        payload={"source": "osm_overpass"},
                    )
        except Exception as exc:  # pragma: no cover
            errors.append(f"discovery: {exc!s}")

    dj.candidates_found = candidates_found
    dj.prospects_created = prospects_created
    dj.finished_at = _now()
    if errors:
        dj.status = "partially_completed"
        dj.error = "; ".join(errors)
    else:
        dj.status = "succeeded"

    job.status = "succeeded" if not errors else "partially_completed"
    job.last_error = "; ".join(errors) if errors else None
    db.flush()


def process_job(db: Session, job: Job) -> None:
    """Process a single claimed job based on its type."""
    if job.type == "research":
        _run_research_job(db, job)
    elif job.type == "discover":
        _run_discover_job(db, job)
    else:
        job.status = "failed"
        job.last_error = f"Unknown job type: {job.type}"
    db.flush()


def schedule_retry(db: Session, job: Job) -> None:
    """Reschedule a failed job for retry with exponential backoff.

    After max_attempts, the job is left in ``failed`` status.
    """
    max_attempts = job.max_attempts or 3
    if job.attempts >= max_attempts:
        job.status = "failed"
        db.flush()
        return

    # Exponential backoff: 2^attempts seconds, capped at 1 hour.
    backoff_seconds = min(2 ** (job.attempts - 1), 3600)
    job.status = "queued"
    job.run_after = _now() + timedelta(seconds=backoff_seconds)
    job.locked_at = None
    db.flush()


def process_one_job(db: Session) -> bool:
    """Claim and process one job. Returns True if a job was processed."""
    job = claim_job(db)
    if job is None:
        return False

    try:
        process_job(db, job)
        if job.status in ("succeeded", "partially_completed"):
            # Already flushed by process_job.
            pass
        elif job.status == "failed":
            schedule_retry(db, job)
    except Exception:  # pragma: no cover - defensive
        job.status = "failed"
        job.last_error = "Unexpected error processing job"
        schedule_retry(db, job)

    return True


def run_worker(once: bool = False) -> None:
    """Run the worker loop.

    When ``once=True``, processes a single available job and exits.
    Otherwise loops forever, polling for new jobs.
    """
    while True:
        db = SessionLocal()
        try:
            processed = process_one_job(db)
        finally:
            db.close()

        if once or not processed:
            if once:
                break
            time.sleep(POLL_INTERVAL_SECONDS)


def queue_research_job(
    db: Session,
    *,
    prospect_id: int,
    research_run_id: int,
) -> Job:
    """Create a queued research job for a prospect."""
    job = Job(
        type="research",
        payload={"prospect_id": prospect_id, "research_run_id": research_run_id},
        status="queued",
    )
    db.add(job)
    db.flush()
    return job


def queue_discover_job(
    db: Session,
    *,
    profile_id: int,
    discovery_job_id: int | None = None,
) -> Job:
    """Create a queued discovery job for a profile."""
    job = Job(
        type="discover",
        payload={"profile_id": profile_id, "discovery_job_id": discovery_job_id},
        status="queued",
    )
    db.add(job)
    db.flush()
    return job
