"""CSV import service (Flow 10, DATABASE_DESIGN §2.1 source type 'csv_import').

Secondary Settings utility — not part of the discovery flow. 3-step wizard:
upload → preview (valid / duplicate / error rows) → commit.

All parsing is offline; no network I/O. Dedupe uses the same exact/fuzzy
checks as manual add (prospect_service.check_duplicates). Exact dupes are
skipped; fuzzy matches are inserted with a ``possible-duplicate`` tag.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Source, Tag
from app.services.activity import log_activity
from app.services.prospect import check_duplicates, create_prospect
from app.whatsapp import normalize_phone

# Maximum uploaded bytes (matches settings.csv_max_bytes).
MAX_UPLOAD_BYTES = settings.csv_max_bytes
# Maximum rows (matches settings.csv_max_rows).
MAX_UPLOAD_ROWS = settings.csv_max_rows

# Required CSV column (at least one alias must be present).
_REQUIRED_COLUMN = "business_name"

# Canonical field -> set of accepted header aliases (case-insensitive).
_COLUMN_ALIASES: dict[str, set[str]] = {
    "business_name": {"business_name", "name", "company", "business"},
    "category": {"category", "industry", "sector"},
    "location_area": {"location_area", "area", "region"},
    "location_city": {"location_city", "city", "town"},
    "location_state": {"location_state", "state", "province"},
    "location_country": {"location_country", "country"},
    "address": {"address", "street", "address_line"},
    "phone_raw": {"phone", "phone_raw", "telephone", "mobile", "tel"},
    "email": {"email", "email_address", "e-mail"},
    "website_url": {"website_url", "website", "url", "site"},
}

# Reverse map: alias -> canonical field.
_ALIAS_TO_FIELD: dict[str, str] = {}
for canonical, aliases in _COLUMN_ALIASES.items():
    for alias in aliases:
        _ALIAS_TO_FIELD[alias.lower()] = canonical


class ImportError(ValueError):
    """Raised when the CSV cannot be parsed or is structurally invalid."""


def _detect_mapping(headers: list[str]) -> dict[str, str | None]:
    """Auto-detect canonical field for each header; return field -> header."""
    mapping: dict[str, str | None] = {field: None for field in _COLUMN_ALIASES}
    used_headers: set[str] = set()

    # First pass: exact alias match.
    for header in headers:
        key = header.strip().lower()
        if key in _ALIAS_TO_FIELD and key not in used_headers:
            field = _ALIAS_TO_FIELD[key]
            if mapping[field] is None:
                mapping[field] = header
                used_headers.add(key)

    # Second pass: substring fallback for unmapped required-ish fields.
    for header in headers:
        key = header.strip().lower()
        if key in used_headers:
            continue
        for field, aliases in _COLUMN_ALIASES.items():
            if mapping[field] is not None:
                continue
            for alias in aliases:
                if alias in key or key in alias:
                    mapping[field] = header
                    used_headers.add(key)
                    break

    return mapping


def _row_to_dict(row: dict[str, str], mapping: dict[str, str | None]) -> dict[str, str | None]:
    """Extract prospect fields from a raw CSV row using the column mapping."""
    out: dict[str, str | None] = {}
    for field, header in mapping.items():
        if header is None:
            out[field] = None
            continue
        value = row.get(header)
        out[field] = value.strip() if value else None
    return out


def _validate_row(
    row_data: dict[str, str | None], row_index: int
) -> tuple[dict[str, str | None], str | None]:
    """Validate a single parsed row. Returns (cleaned, error_message_or_None)."""
    errors: list[str] = []
    name = row_data.get("business_name")
    if not name or not name.strip():
        errors.append("business name is required")
    else:
        name = name.strip()
        if len(name) > 200:
            errors.append("business name must be 200 characters or fewer")

    phone_raw = row_data.get("phone_raw")
    if phone_raw:
        phone_raw = phone_raw.strip()
        if len(phone_raw) > 80:
            errors.append("phone must be 80 characters or fewer")

    email = row_data.get("email")
    if email:
        email = email.strip()
        if len(email) > 254:
            errors.append("email must be 254 characters or fewer")
        elif "@" not in email or "." not in email.split("@")[-1]:
            errors.append("enter a valid email")

    website_url = row_data.get("website_url")
    if website_url:
        website_url = website_url.strip()
        from urllib.parse import urlparse

        try:
            parsed = urlparse(website_url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                errors.append("enter a valid website (http:// or https://)")
        except ValueError:
            errors.append("enter a valid website URL")

    for field, max_len in (
        ("category", 100),
        ("location_area", 100),
        ("location_city", 100),
        ("location_state", 100),
        ("address", 200),
    ):
        value = row_data.get(field)
        if value and len(value.strip()) > max_len:
            errors.append(f"{field.replace('_', ' ')} must be {max_len} characters or fewer")

    # Formula-injection guard (§6): cells starting with =, +, -, or @ are
    # formula-injection vectors when opened in spreadsheet software. Reject
    # them rather than storing or escaping.
    _FORMULA_PREFIXES = ("=", "+", "-", "@")
    for field_name, value in row_data.items():
        if (
            value
            and isinstance(value, str)
            and value.strip()
            and value.strip()[0] in _FORMULA_PREFIXES
        ):
            errors.append(
                f"{field_name.replace('_', ' ')} cannot start with a formula character (= + - @)"
            )

    cleaned = {
        "business_name": name,
        "category": row_data.get("category"),
        "location_area": row_data.get("location_area"),
        "location_city": row_data.get("location_city"),
        "location_state": row_data.get("location_state"),
        "location_country": row_data.get("location_country"),
        "address": row_data.get("address"),
        "phone_raw": phone_raw if phone_raw else None,
        "email": email if email else None,
        "website_url": website_url if website_url else None,
    }

    if errors:
        return cleaned, "; ".join(errors)
    return cleaned, None


def parse_csv(content: bytes, default_country: str | None = None) -> dict[str, Any]:
    """Parse uploaded CSV bytes into a preview-ready structure.

    Returns a dict with:
      columns_detected, column_mapping, rows (each with index, fields,
      status, reason, fuzzy, matched_prospect*), total/valid/duplicate/error counts.

    Raises ImportError on structural problems (missing required column, too
    many rows, bad encoding, empty file).
    """
    if len(content) > MAX_UPLOAD_BYTES:
        raise ImportError(f"File too large: {len(content)} bytes (limit {MAX_UPLOAD_BYTES})")

    # Decode (UTF-8 with BOM handling).
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise ImportError("Unable to decode CSV file (try UTF-8 or Latin-1)") from exc

    if not text.strip():
        raise ImportError("CSV file is empty")

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ImportError("CSV file has no header row")

    headers = [h for h in reader.fieldnames if h and h.strip()]
    if not headers:
        raise ImportError("CSV file has no usable header row")

    mapping = _detect_mapping(headers)
    if mapping["business_name"] is None:
        raise ImportError(
            "CSV must include a business name column (e.g. 'name', 'company', 'business_name')"
        )

    rows_raw: list[dict[str, str]] = []
    for raw_row in reader:
        if len(rows_raw) >= MAX_UPLOAD_ROWS:
            break
        # Skip fully blank rows.
        if not any((v or "").strip() for v in raw_row.values()):
            continue
        rows_raw.append(raw_row)

    if not rows_raw:
        raise ImportError("CSV file has no data rows")

    # Parse + validate each row.
    parsed_rows: list[dict[str, Any]] = []
    counts = {"valid": 0, "duplicate": 0, "error": 0}

    for i, raw_row in enumerate(rows_raw):
        # Map raw CSV columns (original headers) → canonical prospect fields.
        canonical = _row_to_dict(raw_row, mapping)
        row_data, error = _validate_row(canonical, i)
        row_entry: dict[str, Any] = {
            "index": i,
            "fields": row_data,
            "status": "error" if error else "valid",
            "reason": error,
            "fuzzy": False,
            "matched_prospect_id": None,
            "matched_prospect_name": None,
        }

        if error:
            counts["error"] += 1
        else:
            # Normalize phone to E.164 for dedupe + storage.
            phone_raw = row_data.get("phone_raw")
            phone_e164 = normalize_phone(phone_raw, default_country) if phone_raw else None
            row_entry["fields"]["phone_e164"] = phone_e164

            counts["valid"] += 1

        parsed_rows.append(row_entry)

    return {
        "columns_detected": headers,
        "column_mapping": mapping,
        "rows": parsed_rows,
        "total_rows": len(parsed_rows),
        "valid_count": counts["valid"],
        "duplicate_count": counts["duplicate"],
        "error_count": counts["error"],
    }


def assess_duplicates(
    db: Session,
    rows: list[dict[str, Any]],
    default_country: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Run dedupe checks on parsed rows against the live DB.

    Mutates each row entry: sets status/duplicate/fuzzy/matched fields.
    Returns (rows, counts) with updated valid/duplicate/error counts.
    """
    counts = {"valid": 0, "duplicate": 0, "error": 0}
    for row in rows:
        if row["status"] == "error":
            counts["error"] += 1
            continue
        fields = row["fields"]
        dupe = check_duplicates(
            db,
            business_name=fields["business_name"],
            phone_e164=fields.get("phone_e164"),
            email=fields.get("email"),
            website_url=fields.get("website_url"),
            location_city=fields.get("location_city"),
        )

        if dupe["exact"] is not None:
            row["status"] = "duplicate"
            row["reason"] = (
                f"Duplicate of existing prospect '{dupe['exact'].business_name}' "
                f"(id={dupe['exact'].id})"
            )
            row["matched_prospect_id"] = dupe["exact"].id
            row["matched_prospect_name"] = dupe["exact"].business_name
            counts["duplicate"] += 1
        elif dupe["fuzzy"]:
            row["fuzzy"] = True
            row["reason"] = (
                f"Possible duplicate of {len(dupe['fuzzy'])} existing prospect(s); "
                "will be tagged 'possible-duplicate' on import"
            )
            counts["valid"] += 1
        else:
            counts["valid"] += 1

    return rows, counts


def _get_or_create_csv_source(db: Session) -> Source:
    """Return (or create) the 'csv_import' source row."""
    source = db.query(Source).filter(Source.type == "csv_import").first()
    if source is None:
        source = Source(type="csv_import", label="CSV import", detail={})
        db.add(source)
        db.flush()
    return source


def commit_import(
    db: Session,
    *,
    row_indices: list[int],
    all_rows: list[dict[str, Any]],
    default_country: str | None = None,
) -> dict[str, Any]:
    """Commit selected rows to the database.

    Only rows with status 'valid' are inserted. Duplicates and errors are
    skipped. Fuzzy matches get a 'possible-duplicate' tag.

    Returns a result dict with created/skipped/flagged/error counts and
    created prospect IDs.
    """
    source = _get_or_create_csv_source(db)

    created = 0
    skipped_duplicates = 0
    flagged_for_review = 0
    errors = 0
    created_ids: list[int] = []

    # Build a lookup by index.
    rows_by_index = {r["index"]: r for r in all_rows}

    # Re-run dedupe at commit time (live DB state may have changed).
    for index in row_indices:
        row = rows_by_index.get(index)
        if row is None or row["status"] != "valid":
            continue

        fields = row["fields"]
        phone_e164 = fields.get("phone_e164")
        if phone_e164 is None and fields.get("phone_raw"):
            phone_e164 = normalize_phone(fields["phone_raw"], default_country)

        # Live dedupe check.
        dupe = check_duplicates(
            db,
            business_name=fields["business_name"],
            phone_e164=phone_e164,
            email=fields.get("email"),
            website_url=fields.get("website_url"),
            location_city=fields.get("location_city"),
        )
        if dupe["exact"] is not None:
            skipped_duplicates += 1
            continue

        prospect, err = create_prospect(
            db,
            business_name=fields["business_name"],
            source=source,
            category=fields.get("category"),
            location_area=fields.get("location_area"),
            location_city=fields.get("location_city"),
            location_state=fields.get("location_state"),
            location_country=fields.get("location_country"),
            address=fields.get("address"),
            phone_e164=phone_e164,
            phone_raw=fields.get("phone_raw"),
            email=fields.get("email"),
            website_url=fields.get("website_url"),
            social_links=None,
            actor="user",
            skip_dupe_check=True,  # we just checked above
        )
        if err or prospect is None:
            errors += 1
            continue

        created += 1
        created_ids.append(prospect.id)

        # Fuzzy flag → tag.
        if row.get("fuzzy"):
            tag = db.query(Tag).filter(Tag.name == "possible-duplicate").first()
            if tag is None:
                tag = Tag(name="possible-duplicate", color="#b45309")
                db.add(tag)
                db.flush()
            if tag not in prospect.tags:
                prospect.tags.append(tag)
            flagged_for_review += 1

        log_activity(
            db,
            prospect_id=prospect.id,
            type="imported",
            actor="user",
            payload={"source": "csv_import", "row_index": index},
        )

    db.flush()

    return {
        "created": created,
        "skipped_duplicates": skipped_duplicates,
        "flagged_for_review": flagged_for_review,
        "errors": errors,
        "created_ids": created_ids,
    }


def save_import_session(token: str, data: dict[str, Any]) -> str:
    """Persist parsed import data to a temp file; return the file path."""
    settings.ensure_dirs()
    path = settings.uploads_dir / f"import_{token}.json"
    payload = {"data": data, "created_at": datetime.now(UTC).isoformat()}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def load_import_session(token: str) -> dict[str, Any] | None:
    """Load parsed import data from a temp file."""
    path = settings.uploads_dir / f"import_{token}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("data")
    except (json.JSONDecodeError, OSError):
        return None


def delete_import_session(token: str) -> None:
    """Remove a temp import file (best-effort)."""
    with contextlib.suppress(OSError):
        (settings.uploads_dir / f"import_{token}.json").unlink()
