from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import init_db
from app.ingestion import validate_ingest_file
from app.models import Adjustment, Alert, AuditEvent, Batch, MarkerStat, Score, StageRun
from app.pipeline import resume, run_pipeline


def make_db(tmp_path, name="pipeline.db"):
    engine, factory = init_db(f"sqlite:///{tmp_path / name}")
    return engine, factory


def ingest_simple(factory):
    session = factory()
    content = (
        b"student_id,marker_id,subject,raw_score\n"
        + b"".join(
            f"S{i:03d},{'HIGH' if i < 20 else 'LOW'},Math,{100 if i < 20 else 50}\n".encode()
            for i in range(40)
        )
    )
    batch, _, _, _ = validate_ingest_file(session, content, "simple.csv")
    session.close()
    return batch.id


def test_running_twice_has_stable_hash_no_double_adjustments(tmp_path):
    _, factory = make_db(tmp_path)
    batch_id = ingest_simple(factory)

    session = factory()
    first = run_pipeline(batch_id, session)
    first_hash = first["output_hash"]
    count = session.query(Adjustment).filter_by(batch_id=batch_id).count()

    second = run_pipeline(batch_id, session)

    assert second["output_hash"] == first_hash
    assert second["MODERATE"] == "skipped"
    assert second["ALERTS"] == "skipped"
    assert session.query(Adjustment).filter_by(batch_id=batch_id).count() == count


def test_crash_in_moderate_rolls_back_and_resume_matches_clean_run(tmp_path, monkeypatch):
    _, clean_factory = make_db(tmp_path, "clean.db")
    clean_id = ingest_simple(clean_factory)
    clean_session = clean_factory()
    clean = run_pipeline(clean_id, clean_session)
    clean_hash = clean["output_hash"]

    _, crash_factory = make_db(tmp_path, "crash.db")
    crash_id = ingest_simple(crash_factory)
    crash_session = crash_factory()

    def injected_hash(_):
        raise RuntimeError("injected crash during moderate")

    monkeypatch.setattr("app.pipeline.sha256", injected_hash)
    crashed = run_pipeline(crash_id, crash_session)

    assert crashed["MODERATE"] == "failed"
    assert crash_session.query(MarkerStat).filter_by(batch_id=crash_id).count() == 0
    assert crash_session.query(Adjustment).filter_by(batch_id=crash_id).count() == 0
    moderate = crash_session.query(StageRun).filter_by(batch_id=crash_id, stage="MODERATE").one()
    assert moderate.status == "failed"
    assert moderate.error

    monkeypatch.undo()
    resumed = resume(crash_session, crash_id)

    assert resumed["output_hash"] == clean_hash
    assert crash_session.query(MarkerStat).filter_by(batch_id=crash_id).count() > 0


def test_completed_stage_is_never_rerun(tmp_path):
    _, factory = make_db(tmp_path)
    batch_id = ingest_simple(factory)
    session = factory()

    run_pipeline(batch_id, session)
    completed_before = session.query(StageRun).filter_by(
        batch_id=batch_id, stage="MODERATE"
    ).one()

    run_pipeline(batch_id, session)
    completed_after = session.query(StageRun).filter_by(
        batch_id=batch_id, stage="MODERATE"
    ).one()

    assert completed_before.status == "completed"
    assert completed_after.status == "completed"
    started_events = session.query(AuditEvent).filter_by(
        batch_id=batch_id, event_type="STAGE_STARTED"
    ).count()
    assert started_events == 2


def test_two_threads_moderate_once_with_file_database(tmp_path):
    db_path = tmp_path / "concurrent.db"
    engine, factory = init_db(f"sqlite:///{db_path}")
    session = factory()
    content = (
        b"student_id,marker_id,subject,raw_score\n"
        + b"".join(
            f"S{i:03d},{'HIGH' if i < 20 else 'LOW'},Math,{100 if i < 20 else 50}\n".encode()
            for i in range(40)
        )
    )
    batch, _, _, _ = validate_ingest_file(session, content, "concurrent.csv")
    batch_id = batch.id
    session.close()

    def worker():
        worker_session = factory()
        try:
            return run_pipeline(batch_id, worker_session)
        finally:
            worker_session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))

    session = factory()
    count = session.query(Adjustment).filter_by(batch_id=batch_id).count()
    hash_values = {result["output_hash"] for result in results}
    assert count > 0
    assert len(hash_values) == 1


def test_full_sample_file_runs_end_to_end(tmp_path):
    _, factory = make_db(tmp_path)

    sample_path = Path(__file__).resolve().parent.parent / "sample" / "scores_sample.csv"
    session = factory()
    batch, _, valid_count, rejected_count = validate_ingest_file(
        session, sample_path.read_bytes(), sample_path.name
    )

    result = run_pipeline(batch.id, session)

    assert valid_count == 266
    assert rejected_count == 6
    assert len(result["output_hash"]) == 64
    assert session.query(MarkerStat).filter_by(batch_id=batch.id).count() > 0
    assert session.query(Alert).filter_by(batch_id=batch.id).count() > 0
