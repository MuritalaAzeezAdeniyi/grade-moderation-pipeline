from app.database import init_db
from app.ingestion import validate_ingest_file
from app.models import RejectedRow, Score


def test_sample_file_counts_and_marker_properties(tmp_path):
    _, session_factory = init_db(f"sqlite:///{tmp_path / 'sample.db'}")
    session = session_factory()

    from scripts.make_sample_data import main
    main()

    sample_path = tmp_path.parent / "unused"
    # The script writes to the repository sample directory.
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "sample" / "scores_sample.csv"

    batch, duplicate, valid_count, rejected_count = validate_ingest_file(
        session, path.read_bytes(), path.name
    )

    assert duplicate is False
    assert valid_count == 266
    assert rejected_count == 6

    scores = session.query(Score).filter_by(batch_id=batch.id).all()
    assert {score.subject for score in scores} == {"Mathematics", "Science"}
    assert {score.marker_id for score in scores} >= {
        "STRICT", "GENEROUS", "NORMAL_A", "NORMAL_B", "LOW_SPREAD", "SMALL"
    }

    assert session.query(RejectedRow).filter_by(batch_id=batch.id).count() == 6
