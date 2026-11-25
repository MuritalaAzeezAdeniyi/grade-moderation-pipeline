from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.audit import record_event
from app.database import init_db
from app.models import Batch, AuditEvent


def test_init_db_creates_all_step_tables(tmp_path):
    db_path = tmp_path / "test.db"
    engine, session_factory = init_db(f"sqlite:///{db_path}")

    inspector_tables = set(__import__("sqlalchemy").inspect(engine).get_table_names())

    assert {
        "batches", "scores", "rejected_rows", "stage_runs", "marker_stats",
        "adjustments", "alerts", "reviews", "exports", "audit_events",
    } <= inspector_tables


def test_audit_triggers_reject_update_and_delete(tmp_path):
    db_path = tmp_path / "audit.db"
    engine, session_factory = init_db(f"sqlite:///{db_path}")
    session = session_factory()

    batch = Batch(input_hash="x" * 64, original_filename="scores.csv")
    session.add(batch)
    session.flush()
    event = record_event(
        session, batch_id=batch.id, actor="system",
        event_type="BATCH_INGESTED", details={"filename": "scores.csv"},
    )
    session.commit()

    with pytest.raises(IntegrityError):
        session.execute(
            __import__("sqlalchemy").text("UPDATE audit_events SET actor='reviewer' WHERE id=:id"),
            {"id": event.id},
        )
        session.commit()
    session.rollback()

    with pytest.raises(IntegrityError):
        session.execute(
            __import__("sqlalchemy").text("DELETE FROM audit_events WHERE id=:id"),
            {"id": event.id},
        )
        session.commit()
