from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.database import init_db


def make_api(tmp_path, monkeypatch):
    db_path = tmp_path / "review_export.db"

    def test_init_db(database_url=None):
        return init_db(f"sqlite:///{db_path}")

    monkeypatch.setattr(main_module, "init_db", test_init_db)
    return TestClient(main_module.app), db_path


def ingest(client, name, content):
    response = client.post("/batches", files={"file": (name, content, "text/csv")})
    assert response.status_code == 201
    return response.json()["id"]


def test_export_blocked_pending_rejected_and_approved(tmp_path, monkeypatch):
    client, db_path = make_api(tmp_path, monkeypatch)
    rows = ["student_id,marker_id,subject,raw_score"]
    for i in range(20):
        rows.append(f"A{i:02d},HIGH,Math,{100}")
    for i in range(20):
        rows.append(f"B{i:02d},LOW,Math,{50}")
    batch_id = ingest(client, "review.csv", ("\n".join(rows) + "\n").encode())

    assert client.post(f"/batches/{batch_id}/run").status_code == 200
    pairs = client.get(f"/batches/{batch_id}/adjustments").json()
    assert pairs

    blocked = client.post(f"/batches/{batch_id}/export")
    assert blocked.status_code == 409

    pair_ids = sorted({(row["marker_id"], row["subject"] ) for row in pairs})
    assert pair_ids

    # Reject the first pair and approve the second, if present.
    decisions = ["REJECTED", "APPROVED"]
    for index, (marker, subject) in enumerate(pair_ids):
        response = client.post(f"/batches/{batch_id}/review", json={
            "marker_id": marker,
            "subject": subject,
            "decision": decisions[index if index < len(decisions) else 0],
            "reviewer": "Reviewer One",
        })
        assert response.status_code == 200

    exported = client.post(f"/batches/{batch_id}/export")
    assert exported.status_code == 200

    download = client.get(f"/batches/{batch_id}/export/download")
    assert download.status_code == 200
    csv_text = download.text
    assert "student_id,marker_id,subject,raw_score,final_score,adjusted,rule_version,batch_id" in csv_text

    if ("LOW", "Math") in pair_ids:
        low_lines = [line for line in csv_text.splitlines() if ",LOW,Math," in line]
        assert low_lines
    repeated = client.post(f"/batches/{batch_id}/export")
    assert repeated.status_code == 200
    assert repeated.json()["sha256"] == exported.json()["sha256"]

    _, factory = init_db(f"sqlite:///{db_path}")
    session = factory()
    from app.models import Export
    assert session.query(Export).filter_by(batch_id=batch_id).count() == 1


def test_rejected_exports_raw_and_approved_exports_adjusted(tmp_path, monkeypatch):
    client, db_path = make_api(tmp_path, monkeypatch)
    rows = ["student_id,marker_id,subject,raw_score"]
    for i in range(20):
        rows.append(f"A{i:02d},HIGH,Math,{100}")
    for i in range(20):
        rows.append(f"B{i:02d},LOW,Math,{50}")
    batch_id = ingest(client, "scores.csv", ("\n".join(rows) + "\n").encode())
    client.post(f"/batches/{batch_id}/run")

    adjustments = client.get(f"/batches/{batch_id}/adjustments").json()
    pair_ids = {(row["marker_id"], row["subject"] ) for row in adjustments}
    assert pair_ids

    for index, (marker, subject) in enumerate(sorted(pair_ids)):
        decision = "APPROVED" if index == 0 else "REJECTED"
        assert client.post(f"/batches/{batch_id}/review", json={
            "marker_id": marker, "subject": subject,
            "decision": decision, "reviewer": "Reviewer",
        }).status_code == 200

    assert client.post(f"/batches/{batch_id}/export").status_code == 200
    text = client.get(f"/batches/{batch_id}/export/download").text
    lines = text.splitlines()[1:]

    approved_marker = sorted(pair_ids)[0][0]
    approved_rows = [line for line in lines if f",{approved_marker},Math," in line]
    rejected_marker = sorted(pair_ids)[1][0] if len(pair_ids) > 1 else None
    if approved_rows:
        assert "true" in approved_rows[0]
    if rejected_marker:
        rejected_rows = [line for line in lines if f",{rejected_marker},Math," in line]
        assert rejected_rows and "false" in rejected_rows[0]


def test_unknown_marker_400_and_second_decision_409(tmp_path, monkeypatch):
    client, db_path = make_api(tmp_path, monkeypatch)
    sample = Path(__file__).resolve().parent.parent / "sample" / "scores_sample.csv"
    batch_id = ingest(client, sample.name, sample.read_bytes())
    client.post(f"/batches/{batch_id}/run")

    unknown = client.post(f"/batches/{batch_id}/review", json={
        "marker_id": "NO_SUCH_MARKER", "subject": "Science",
        "decision": "APPROVED", "reviewer": "Reviewer",
    })
    assert unknown.status_code == 400

    adjustments = client.get(f"/batches/{batch_id}/adjustments").json()
    marker, subject = adjustments[0]["marker_id"], adjustments[0]["subject"]
    first = client.post(f"/batches/{batch_id}/review", json={
        "marker_id": marker, "subject": subject,
        "decision": "APPROVED", "reviewer": "Reviewer",
    })
    assert first.status_code == 200

    second = client.post(f"/batches/{batch_id}/review", json={
        "marker_id": marker, "subject": subject,
        "decision": "REJECTED", "reviewer": "Reviewer Two",
    })
    assert second.status_code == 409


def test_export_batch_with_no_adjustments(tmp_path, monkeypatch):
    client, db_path = make_api(tmp_path, monkeypatch)
    batch_id = ingest(client, "normal.csv", (
        "student_id,marker_id,subject,raw_score\n"
        + "".join(f"S{i:02d},NORMAL,Math,60\n" for i in range(20))
    ).encode())
    assert client.post(f"/batches/{batch_id}/run").status_code == 200
    assert client.get(f"/batches/{batch_id}/adjustments").json() == []

    exported = client.post(f"/batches/{batch_id}/export")
    assert exported.status_code == 200
    assert exported.json()["row_count"] == 20

    download = client.get(f"/batches/{batch_id}/export/download")
    assert download.status_code == 200
