from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.database import init_db
from app.models import Adjustment, Alert, AuditEvent, StageRun


@pytest.fixture
def api(tmp_path, monkeypatch):
    db_path = tmp_path / "api.db"

    def test_init_db(database_url=None):
        return init_db(f"sqlite:///{db_path}")

    monkeypatch.setattr(main_module, "init_db", test_init_db)
    with TestClient(main_module.app) as client:
        yield client, db_path


def sample_upload(api):
    client, db_path = api
    sample = Path(__file__).resolve().parent.parent / "sample" / "scores_sample.csv"
    with sample.open("rb") as handle:
        return client.post("/batches", files={"file": (sample.name, handle, "text/csv")})


def test_upload_once_twice_run_and_read(api):
    client, db_path = api

    first = sample_upload(api)
    assert first.status_code == 201
    batch_id = first.json()["id"]

    duplicate = sample_upload(api)
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == batch_id

    listed = client.get("/batches")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    detail = client.get(f"/batches/{batch_id}")
    assert detail.status_code == 200

    run = client.post(f"/batches/{batch_id}/run")
    assert run.status_code == 200

    adjustments = client.get(f"/batches/{batch_id}/adjustments")
    assert adjustments.status_code == 200

    alerts = client.get(f"/batches/{batch_id}/alerts")
    assert alerts.status_code == 200

    rejected = client.get(f"/batches/{batch_id}/rejected-rows")
    assert rejected.status_code == 200
    assert len(rejected.json()) == 6

    audit = client.get(f"/batches/{batch_id}/audit")
    assert audit.status_code == 200


def test_adjustment_filters(api):
    client, db_path = api
    batch_id = sample_upload(api).json()["id"]
    assert client.post(f"/batches/{batch_id}/run").status_code == 200

    first = client.get(f"/batches/{batch_id}/adjustments", params={"marker_id": "STRICT"})
    assert first.status_code == 200
    assert all(row["marker_id"] == "STRICT" for row in first.json())

    second = client.get(f"/batches/{batch_id}/adjustments", params={"subject": "Mathematics"})
    assert second.status_code == 200
    assert all(row["subject"] == "Mathematics" for row in second.json())


def test_bad_uploads_return_400(api):
    client, db_path = api

    wrong_header = client.post("/batches", files={"file": ("bad.csv", b"student,marker,subject,raw_score\nS,M,X,50\n", "text/csv")})
    assert wrong_header.status_code == 400
    assert wrong_header.json()["error"].startswith("Wrong CSV header")

    no_valid = client.post("/batches", files={"file": ("bad.csv", b"student_id,marker_id,subject,raw_score\nS,M,X,nope\n", "text/csv")})
    assert no_valid.status_code == 400
    assert no_valid.json() == {"error": "CSV has no valid rows"}

    missing_file = client.post("/batches")
    assert missing_file.status_code == 400
    assert missing_file.json() == {"error": "Invalid input"}


def test_unknown_batch_returns_404(api):
    client, db_path = api
    for path in (
        "/batches/999",
        "/batches/999/run",
        "/batches/999/resume",
        "/batches/999/adjustments",
        "/batches/999/alerts",
        "/batches/999/rejected-rows",
        "/batches/999/audit",
    ):
        method = client.post if path.endswith(("/run", "/resume")) else client.get
        response = method(path)
        assert response.status_code == 404
        assert response.json() == {"error": "Batch not found"}


def test_run_wrong_state_returns_409(api):
    client, db_path = api
    batch_id = sample_upload(api).json()["id"]
    _, factory = init_db(f"sqlite:///{db_path}")
    session = factory()
    from app.models import StageRun
    session.add(StageRun(batch_id=batch_id, stage="MODERATE", status="in_progress"))
    session.add(StageRun(batch_id=batch_id, stage="ALERTS", status="pending"))
    session.commit()
    session.close()

    response = client.post(f"/batches/{batch_id}/run")
    assert response.status_code == 409
    assert response.json()["error"] == "Batch is in a wrong state; use resume"


def test_resume_after_simulated_failure(api, monkeypatch):
    client, db_path = api
    batch_id = sample_upload(api).json()["id"]

    _, factory = init_db(f"sqlite:///{db_path}")
    session = factory()
    session.add(StageRun(batch_id=batch_id, stage="MODERATE", status="failed", error="simulated"))
    session.add(StageRun(batch_id=batch_id, stage="ALERTS", status="pending"))
    session.commit()
    session.close()

    response = client.post(f"/batches/{batch_id}/resume")

    assert response.status_code == 200
    assert len(response.json()["output_hash"]) == 64
