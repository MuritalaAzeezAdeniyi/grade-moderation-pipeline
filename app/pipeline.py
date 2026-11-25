import json
import time
from hashlib import sha256
from typing import Any

from sqlalchemy import and_, update
from sqlalchemy.orm import Session

from app.alerts import build_alerts
from app.audit import record_event
from app.engine import compute_adjustments, compute_stats
from app.models import Adjustment, Alert, Batch, MarkerStat, Score, StageRun
from app.rules import Rules, load_rules


STAGES = ("MODERATE", "ALERTS")


def _canonical_adjustments(adjustments: list[dict[str, Any]]) -> str:
    canonical = []
    for item in sorted(adjustments, key=lambda row: (row["subject"], row["student_id"])):
        canonical.append({
            "student_id": item["student_id"],
            "marker_id": item["marker_id"],
            "subject": item["subject"],
            "raw_score": str(item["raw_score"]),
            "delta": item["delta"],
            "adjusted_score": item["adjusted_score"],
            "grade_before": item["grade_before"],
            "grade_after": item["grade_after"],
            "rule_id": item["rule_id"],
            "facts": item["facts"],
            "status": item["status"],
        })
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _resolve_session_and_batch(batch_or_session: Any, session_or_batch: Any) -> tuple[Session, int]:
    if isinstance(batch_or_session, Session):
        return batch_or_session, int(session_or_batch)
    if isinstance(session_or_batch, Session):
        return session_or_batch, int(batch_or_session)
    raise TypeError("run_pipeline requires a batch_id and SQLAlchemy Session")


def ensure_stages(session: Session, batch_id: int) -> None:
    for stage in STAGES:
        if session.query(StageRun).filter_by(batch_id=batch_id, stage=stage).one_or_none() is None:
            session.add(StageRun(batch_id=batch_id, stage=stage, status="pending"))
    session.commit()


def _claim_stage(session: Session, batch_id: int, stage: str) -> bool:
    # Conditional UPDATE is the atomic claim: exactly one caller can change
    # pending -> in_progress.
    result = session.execute(
        update(StageRun)
        .where(and_(
            StageRun.batch_id == batch_id,
            StageRun.stage == stage,
            StageRun.status == "pending",
        ))
        .values(status="in_progress", error=None)
    )
    if result.rowcount != 1:
        session.rollback()
        return False

    record_event(
        session,
        batch_id=batch_id,
        actor="system",
        event_type="STAGE_STARTED",
        details={"stage": stage},
    )
    session.commit()
    return True


def _rules_for_batch(batch: Batch) -> Rules:
    if batch.rules_snapshot_json:
        snapshot = json.loads(batch.rules_snapshot_json)
        return Rules.model_validate(snapshot)
    rules, _ = load_rules()
    return rules


def _store_batch_rules(session: Session, batch: Batch) -> Rules:
    rules, rules_hash = load_rules()
    import json as _json
    from pathlib import Path

    rules_path = Path(__file__).resolve().parent.parent / "rules" / "rules_v1.json"
    snapshot = rules_path.read_text(encoding="utf-8")
    batch.rules_snapshot_json = snapshot
    batch.rules_sha256 = rules_hash
    return rules


def _fail_stage(session: Session, batch_id: int, stage: str, error: BaseException) -> None:
    session.rollback()
    run = session.query(StageRun).filter_by(batch_id=batch_id, stage=stage).one()
    run.status = "failed"
    run.error = str(error)
    record_event(
        session,
        batch_id=batch_id,
        actor="system",
        event_type="STAGE_FAILED",
        details={"stage": stage, "error": str(error)},
    )
    session.commit()


def _run_moderate(session: Session, batch_id: int) -> None:
    batch = session.query(Batch).filter_by(id=batch_id).one()
    rules = _store_batch_rules(session, batch)

    scores = session.query(Score).filter_by(batch_id=batch_id).all()
    stats = compute_stats(scores, rules)
    adjustments = compute_adjustments(scores, stats, rules)

    session.query(MarkerStat).filter_by(batch_id=batch_id).delete(synchronize_session=False)
    session.query(Adjustment).filter_by(batch_id=batch_id).delete(synchronize_session=False)

    for stat in stats:
        session.add(MarkerStat(
            batch_id=batch_id,
            marker_id=stat["marker_id"],
            subject=stat["subject"],
            n=stat["n"],
            mean=stat["mean"],
            stdev=stat["stdev"],
            cohort_mean=stat["cohort_mean"],
            deviation=stat["deviation"],
        ))

    for item in adjustments:
        session.add(Adjustment(
            batch_id=batch_id,
            student_id=item["student_id"],
            marker_id=item["marker_id"],
            subject=item["subject"],
            raw_score=item["raw_score"],
            delta=item["delta"],
            adjusted_score=item["adjusted_score"],
            grade_before=item["grade_before"],
            grade_after=item["grade_after"],
            rule_id=item["rule_id"],
            facts_json=json.dumps(item["facts"], sort_keys=True, separators=(",", ":")),
            status=item["status"],
        ))

    batch.output_hash = sha256(_canonical_adjustments(adjustments).encode("utf-8")).hexdigest()


def _run_alerts(session: Session, batch_id: int) -> None:
    batch = session.query(Batch).filter_by(id=batch_id).one()
    rules = _rules_for_batch(batch)

    stat_rows = session.query(MarkerStat).filter_by(batch_id=batch_id).all()
    stats = [{
        "marker_id": row.marker_id,
        "subject": row.subject,
        "n": row.n,
        "mean": row.mean,
        "stdev": row.stdev,
        "cohort_mean": row.cohort_mean,
        "deviation": row.deviation,
    } for row in stat_rows]

    adjustment_rows = session.query(Adjustment).filter_by(batch_id=batch_id).all()
    adjustments = [{
        "marker_id": row.marker_id,
        "subject": row.subject,
        "raw_score": row.raw_score,
        "adjusted_score": row.adjusted_score,
        "grade_before": row.grade_before,
        "grade_after": row.grade_after,
    } for row in adjustment_rows]

    alert_rows = build_alerts(stats, adjustments, rules)
    session.query(Alert).filter_by(batch_id=batch_id).delete(synchronize_session=False)
    for item in alert_rows:
        session.add(Alert(
            batch_id=batch_id,
            marker_id=item["marker_id"],
            subject=item["subject"],
            alert_type=item["alert_type"],
            severity=item["severity"],
            details_json=json.dumps(item["details"], sort_keys=True, separators=(",", ":")),
        ))


def resume(session: Session, batch_id: int) -> dict[str, str]:
    session.query(StageRun).filter(
        and_(StageRun.batch_id == batch_id, StageRun.status.in_(["in_progress", "failed"]))
    ).update({"status": "pending", "error": None}, synchronize_session=False)
    session.commit()
    return run_pipeline(batch_id, session)


def run_pipeline(batch_or_session: Any, session_or_batch: Any = None) -> dict[str, str]:
    if isinstance(batch_or_session, Session):
        session, batch_id = _resolve_session_and_batch(batch_or_session, session_or_batch)
    else:
        batch_id = int(batch_or_session)
        if session_or_batch is None:
            raise TypeError("A SQLAlchemy Session is required")
        session = session_or_batch

    if session.query(Batch).filter_by(id=batch_id).one_or_none() is None:
        raise LookupError(f"Batch {batch_id} not found")

    ensure_stages(session, batch_id)

    results: dict[str, str] = {}
    for stage in STAGES:
        run = session.query(StageRun).filter_by(batch_id=batch_id, stage=stage).one()
        if run.status == "completed":
            results[stage] = "skipped"
            continue

        claimed = _claim_stage(session, batch_id, stage)
        if not claimed:
            results[stage] = "skipped_claimed_by_other_run"
            continue

        try:
            if stage == "MODERATE":
                _run_moderate(session, batch_id)
            else:
                _run_alerts(session, batch_id)

            run = session.query(StageRun).filter_by(batch_id=batch_id, stage=stage).one()
            run.status = "completed"
            run.finished_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            run.error = None
            record_event(
                session,
                batch_id=batch_id,
                actor="system",
                event_type="STAGE_COMPLETED",
                details={"stage": stage},
            )
            session.commit()
            results[stage] = "completed"
        except BaseException as exc:
            _fail_stage(session, batch_id, stage, exc)
            results[stage] = "failed"

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        batch = session.query(Batch).filter_by(id=batch_id).one()
        if batch.output_hash:
            break
        moderate = session.query(StageRun).filter_by(batch_id=batch_id, stage="MODERATE").one_or_none()
        if moderate is None or moderate.status != "in_progress":
            break
        time.sleep(0.01)

    batch = session.query(Batch).filter_by(id=batch_id).one()
    results["output_hash"] = batch.output_hash or ""
    return results
