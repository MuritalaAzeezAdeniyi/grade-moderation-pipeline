import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

os.makedirs("tests/golden", exist_ok=True)
os.environ["DATABASE_URL"] = "sqlite:///tests/golden/golden.db"

from fastapi.testclient import TestClient

from app.database import init_db
from app.main import app


golden = root / "tests" / "golden"

init_db()
client = TestClient(app)
sample = root / "sample" / "scores_sample.csv"

with sample.open("rb") as handle:
    upload = client.post("/batches", files={"file": (sample.name, handle, "text/csv")})

batch_id = upload.json()["id"]
run = client.post(f"/batches/{batch_id}/run")
adjustments = client.get(f"/batches/{batch_id}/adjustments").json()
alerts = client.get(f"/batches/{batch_id}/alerts").json()

pairs = sorted({(row["marker_id"], row["subject"]) for row in adjustments})
for marker_id, subject in pairs:
    client.post(
        f"/batches/{batch_id}/review",
        json={"marker_id": marker_id, "subject": subject, "decision": "APPROVED", "reviewer": "golden"},
    )

export = client.post(f"/batches/{batch_id}/export")
download = client.get(f"/batches/{batch_id}/export/download")

(golden / "adjustments.json").write_text(json.dumps(adjustments, indent=2, sort_keys=True) + "\n", encoding="utf-8")
(golden / "alerts.json").write_text(json.dumps(alerts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
(golden / "export.csv").write_text(download.text, encoding="utf-8", newline="")

print("upload_status", upload.status_code)
print("run", run.status_code, run.json())
print("adjustment_count", len(adjustments))
print("alert_count", len(alerts))
print("pairs", pairs)
print("export_status", export.status_code)
print("export", export.json())
print("export_lines", len(download.text.splitlines()))
