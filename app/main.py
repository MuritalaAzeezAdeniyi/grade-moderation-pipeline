import json
from decimal import Decimal

from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.database import init_db
from app.ingestion import validate_ingest_file
from app.models import Adjustment, Alert, AuditEvent, Batch, RejectedRow, StageRun
from app.pipeline import resume, run_pipeline


app = FastAPI(title="Grade Moderation Pipeline")


def _decimal(value):
    return str(value) if isinstance(value, Decimal) else value


def _batch_json(batch: Batch) -> dict:
    return {
        "id": batch.id,
        "input_hash": batch.input_hash,
        "original_filename": batch.original_filename,
        "status": batch.status,
        "rules_sha256": batch.rules_sha256,
        "output_hash": batch.output_hash,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
    }


def _get_batch(session, batch_id: int) -> Batch:
    batch = session.query(Batch).filter_by(id=batch_id).one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(status_code=exc.status_code, content={"error": message})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": "Invalid input"})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/batches")
async def create_batch(file: UploadFile = File(...)):
    _, session_factory = init_db()
    session = session_factory()
    try:
        file_bytes = await file.read()
        batch, duplicate, valid_count, rejected_count = validate_ingest_file(
            session, file_bytes, file.filename or "upload.csv"
        )
        body = _batch_json(batch)
        body.update({"valid_rows": valid_count, "rejected_rows": rejected_count})
        return JSONResponse(status_code=200 if duplicate else 201, content=body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        session.close()


@app.get("/batches")
async def list_batches():
    _, session_factory = init_db()
    session = session_factory()
    try:
        return [_batch_json(batch) for batch in session.query(Batch).order_by(Batch.id).all()]
    finally:
        session.close()


@app.get("/batches/{batch_id}")
async def get_batch(batch_id: int):
    _, session_factory = init_db()
    session = session_factory()
    try:
        return _batch_json(_get_batch(session, batch_id))
    finally:
        session.close()


@app.post("/batches/{batch_id}/run")
async def run_batch(batch_id: int):
    _, session_factory = init_db()
    session = session_factory()
    try:
        _get_batch(session, batch_id)
        blocked = session.query(StageRun).filter(
            StageRun.batch_id == batch_id,
            StageRun.status.in_(["in_progress", "failed"]),
        ).count()
        if blocked:
            raise HTTPException(status_code=409, detail="Batch is in a wrong state; use resume")
        return run_pipeline(batch_id, session)
    finally:
        session.close()


@app.post("/batches/{batch_id}/resume")
async def resume_batch(batch_id: int):
    _, session_factory = init_db()
    session = session_factory()
    try:
        _get_batch(session, batch_id)
        return resume(session, batch_id)
    finally:
        session.close()


@app.get("/batches/{batch_id}/adjustments")
async def get_adjustments(batch_id: int, marker_id: str | None = None, subject: str | None = None):
    _, session_factory = init_db()
    session = session_factory()
    try:
        _get_batch(session, batch_id)
        query = session.query(Adjustment).filter(Adjustment.batch_id == batch_id)
        if marker_id is not None:
            query = query.filter(Adjustment.marker_id == marker_id)
        if subject is not None:
            query = query.filter(Adjustment.subject == subject)
        rows = query.order_by(Adjustment.subject, Adjustment.student_id).all()
        return [{
            "student_id": row.student_id,
            "marker_id": row.marker_id,
            "subject": row.subject,
            "raw_score": _decimal(row.raw_score),
            "delta": row.delta,
            "adjusted_score": row.adjusted_score,
            "grade_before": row.grade_before,
            "grade_after": row.grade_after,
            "rule_id": row.rule_id,
            "facts": json.loads(row.facts_json),
            "status": row.status,
        } for row in rows]
    finally:
        session.close()


@app.get("/batches/{batch_id}/alerts")
async def get_alerts(batch_id: int):
    _, session_factory = init_db()
    session = session_factory()
    try:
        _get_batch(session, batch_id)
        rows = session.query(Alert).filter_by(batch_id=batch_id).order_by(
            Alert.subject, Alert.marker_id, Alert.alert_type
        ).all()
        return [{
            "marker_id": row.marker_id,
            "subject": row.subject,
            "alert_type": row.alert_type,
            "severity": row.severity,
            "details": json.loads(row.details_json),
        } for row in rows]
    finally:
        session.close()


@app.get("/batches/{batch_id}/rejected-rows")
async def get_rejected_rows(batch_id: int):
    _, session_factory = init_db()
    session = session_factory()
    try:
        _get_batch(session, batch_id)
        rows = session.query(RejectedRow).filter_by(batch_id=batch_id).order_by(RejectedRow.line_number).all()
        return [{"line_number": row.line_number, "reason_code": row.reason_code, "original_text": row.original_text} for row in rows]
    finally:
        session.close()


@app.get("/batches/{batch_id}/audit")
async def get_audit(batch_id: int):
    _, session_factory = init_db()
    session = session_factory()
    try:
        _get_batch(session, batch_id)
        rows = session.query(AuditEvent).filter_by(batch_id=batch_id).order_by(AuditEvent.id).all()
        return [{
            "id": row.id,
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            "batch_id": row.batch_id,
            "actor": row.actor,
            "event_type": row.event_type,
            "details": json.loads(row.details_json),
        } for row in rows]
    finally:
        session.close()
