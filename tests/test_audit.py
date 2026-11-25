from app.audit import record_event
from app.database import init_db
from app.models import Batch


def test_record_event_writes_deterministic_json(tmp_path):
    db_path = tmp_path / "audit.db"
    _, session_factory = init_db(f"sqlite:///{db_path}")
    session = session_factory()

    batch = Batch(input_hash="y" * 64, original_filename="scores.csv")
    session.add(batch)
    session.flush()
    event = record_event(
        session,
        batch_id=batch.id,
        actor="system",
        event_type="BATCH_INGESTED",
        details={"b": 2, "a": 1},
    )
    session.commit()

    assert event.details_json == '{"a":1,"b":2}'
