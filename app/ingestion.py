import csv
import hashlib
import io
import re
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.audit import record_event
from app.models import Batch, RejectedRow, Score, StageRun
from app.rules import load_rules


REQUIRED_HEADER = ["student_id", "marker_id", "subject", "raw_score"]
NUMBER_FORMAT = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")


def _original_text(row: list[str]) -> str:
    return ",".join(row)


def validate_ingest_file(
    session: Session,
    file_bytes: bytes,
    filename: str,
) -> tuple[Batch, bool, int, int]:
    """Ingest a CSV once; return (batch, duplicate_upload, valid_count, rejected_count)."""
    if not filename:
        raise ValueError("Filename is required")

    file_hash = hashlib.sha256(file_bytes).hexdigest()
    existing = session.query(Batch).filter(Batch.input_hash == file_hash).one_or_none()
    if existing is not None:
        record_event(
            session,
            batch_id=existing.id,
            actor="system",
            event_type="DUPLICATE_UPLOAD",
            details={"input_hash": file_hash, "filename": filename},
        )
        session.commit()
        return existing, True, session.query(Score).filter(Score.batch_id == existing.id).count(), session.query(RejectedRow).filter(RejectedRow.batch_id == existing.id).count()

    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("CSV file must be UTF-8 encoded")

    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("Wrong CSV header: required columns are student_id, marker_id, subject, raw_score")

    trimmed_header = [column.strip() for column in header]
    if trimmed_header != REQUIRED_HEADER:
        raise ValueError("Wrong CSV header: required columns are student_id, marker_id, subject, raw_score")

    _, rules_hash = load_rules()
    from pathlib import Path
    rules_path = Path(__file__).resolve().parent.parent / "rules" / "rules_v1.json"
    rules_snapshot = rules_path.read_text(encoding="utf-8")

    # Parse and validate every data row before deciding whether to create a batch.
    valid_rows: list[tuple[str, str, str, Decimal]] = []
    rejected: list[tuple[int, str, str]] = []
    seen_keys: set[tuple[str, str]] = set()

    for line_number, row in enumerate(reader, start=2):
        original_text = _original_text(row)
        padded = (row + [""] * 4)[:4]
        student_id, marker_id, subject, raw_text = [value.strip() for value in padded]

        if not all((student_id, marker_id, subject, raw_text)):
            rejected.append((line_number, "MISSING_FIELD", original_text))
            continue

        if not NUMBER_FORMAT.fullmatch(raw_text):
            rejected.append((line_number, "INVALID_SCORE", original_text))
            continue

        try:
            raw_score = Decimal(raw_text)
        except InvalidOperation:
            rejected.append((line_number, "INVALID_SCORE", original_text))
            continue

        if raw_score.as_tuple().exponent < -2:
            rejected.append((line_number, "INVALID_SCORE", original_text))
            continue

        if raw_score < Decimal("0") or raw_score > Decimal(str(load_rules()[0].max_score)):
            rejected.append((line_number, "OUT_OF_RANGE", original_text))
            continue

        key = (student_id, subject)
        if key in seen_keys:
            rejected.append((line_number, "DUPLICATE_ROW", original_text))
            continue

        seen_keys.add(key)
        valid_rows.append((student_id, marker_id, subject, raw_score))

    if not valid_rows:
        raise ValueError("CSV has no valid rows")

    batch = Batch(
        input_hash=file_hash,
        original_filename=filename,
        status="pending",
        rules_snapshot_json=rules_snapshot,
        rules_sha256=rules_hash,
    )
    session.add(batch)
    session.flush()

    for student_id, marker_id, subject, raw_score in valid_rows:
        session.add(Score(
            batch_id=batch.id,
            student_id=student_id,
            marker_id=marker_id,
            subject=subject,
            raw_score=raw_score,
        ))

    for line_number, reason_code, original_text in rejected:
        session.add(RejectedRow(
            batch_id=batch.id,
            line_number=line_number,
            reason_code=reason_code,
            original_text=original_text,
        ))

    record_event(
        session,
        batch_id=batch.id,
        actor="system",
        event_type="BATCH_INGESTED",
        details={
            "filename": filename,
            "input_hash": file_hash,
            "valid_rows": len(valid_rows),
            "rejected_rows": len(rejected),
        },
    )
    session.commit()
    return batch, False, len(valid_rows), len(rejected)
