import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.database import init_db


def make_api(tmp_path, monkeypatch, name="edge.db"):
    db_path = tmp_path / name

    def test_init_db(database_url=None):
        return init_db(f"sqlite:///{db_path}")

    monkeypatch.setattr(main_module, "init_db", test_init_db)
    return TestClient(main_module.app)


def ingest(client, name, content):
    response = client.post("/batches", files={"file": (name, content, "text/csv")})
    assert response.status_code == 201
    return response.json()["id"]


def test_sample_golden_regression(tmp_path, monkeypatch):
    client = make_api(tmp_path, monkeypatch, "golden_test.db")
    sample = Path(__file__).resolve().parent.parent / "sample" / "scores_sample.csv"
    batch_id = ingest(client, sample.name, sample.read_bytes())

    assert client.post(f"/batches/{batch_id}/run").status_code == 200
    adjustments = client.get(f"/batches/{batch_id}/adjustments").json()
    alerts = client.get(f"/batches/{batch_id}/alerts").json()

    golden_dir = Path(__file__).resolve().parent / "golden"
    expected_adjustments = json.loads((golden_dir / "adjustments.json").read_text(encoding="utf-8"))
    expected_alerts = json.loads((golden_dir / "alerts.json").read_text(encoding="utf-8"))

    assert adjustments == expected_adjustments
    assert alerts == expected_alerts

    pairs = sorted({(row["marker_id"], row["subject"]) for row in adjustments})
    for marker_id, subject in pairs:
        assert client.post(f"/batches/{batch_id}/review", json={
            "marker_id": marker_id,
            "subject": subject,
            "decision": "APPROVED",
            "reviewer": "golden",
        }).status_code == 200

    assert client.post(f"/batches/{batch_id}/export").status_code == 200
    expected_export = (golden_dir / "export.csv").read_text(encoding="utf-8")
    actual_export = client.get(f"/batches/{batch_id}/export/download").text

    if batch_id == 1:
        assert actual_export == expected_export
    else:
        assert actual_export.splitlines()[0] == expected_export.splitlines()[0]
        assert len(actual_export.splitlines()) == len(expected_export.splitlines())


def test_10000_rows_completes_in_reasonable_time(tmp_path, monkeypatch):
    client = make_api(tmp_path, monkeypatch, "large.db")
    lines = ["student_id,marker_id,subject,raw_score"]
    for i in range(10_000):
        marker = f"M{i % 10}"
        lines.append(f"S{i:05d},{marker},Math,{50 + (i % 21)}")

    started = time.monotonic()
    batch_id = ingest(client, "large.csv", ("\n".join(lines) + "\n").encode())
    response = client.post(f"/batches/{batch_id}/run")
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < 20.0


def test_scores_accept_two_decimal_places(tmp_path, monkeypatch):
    client = make_api(tmp_path, monkeypatch, "decimals.db")
    content = "student_id,marker_id,subject,raw_score\n"
    content += "".join(f"S{i:02d},NORMAL,Math,{60 + (i % 2) * 0.5:.2f}\n" for i in range(20))
    batch_id = ingest(client, "decimals.csv", content.encode())

    assert client.post(f"/batches/{batch_id}/run").status_code == 200
    assert client.get(f"/batches/{batch_id}/adjustments").json() == []
    assert client.post(f"/batches/{batch_id}/export").status_code == 200


def test_every_marker_within_threshold_exports_immediately(tmp_path, monkeypatch):
    client = make_api(tmp_path, monkeypatch, "within.db")
    content = "student_id,marker_id,subject,raw_score\n"
    content += "".join(f"A{i:02d},MARKERA,Math,{61 if i % 2 == 0 else 59}\n" for i in range(20))
    content += "".join(f"B{i:02d},MARKERB,Math,{60}\n" for i in range(20))
    batch_id = ingest(client, "within.csv", content.encode())

    assert client.post(f"/batches/{batch_id}/run").status_code == 200
    assert client.get(f"/batches/{batch_id}/adjustments").json() == []
    exported = client.post(f"/batches/{batch_id}/export")
    assert exported.status_code == 200


def test_single_marker_has_no_deviation_and_exports(tmp_path, monkeypatch):
    client = make_api(tmp_path, monkeypatch, "single.db")
    content = "student_id,marker_id,subject,raw_score\n"
    content += "".join(f"S{i:02d},ONLY,Science,{70}\n" for i in range(20))
    batch_id = ingest(client, "single.csv", content.encode())

    assert client.post(f"/batches/{batch_id}/run").status_code == 200
    assert client.get(f"/batches/{batch_id}/adjustments").json() == []
    assert client.post(f"/batches/{batch_id}/export").status_code == 200
