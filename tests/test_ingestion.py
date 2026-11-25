from decimal import Decimal

import pytest

from app.database import init_db
from app.ingestion import validate_ingest_file
from app.models import AuditEvent, Batch, RejectedRow, Score


HEADER = b"student_id,marker_id,subject,raw_score\n"


def make_session(tmp_path):
    _, session_factory = init_db(f"sqlite:///{tmp_path / 'ingestion.db'}")
    return session_factory()


def test_all_rejection_codes_and_trimming(tmp_path):
    session = make_session(tmp_path)
    content = (HEADER + b" S1 , M1 , Mathematics , 50.25 \n"
               b"S2,M1,Science,abc\n"
               b"S3,M1,Science,-1\n"
               b"S4,M1,Science,101\n"
               b"S5,M1,Science,\n"
               b"S1,M1,Mathematics,60\n")

    batch, duplicate, valid_count, rejected_count = validate_ingest_file(
        session, content, "trim.csv"
    )

    assert duplicate is False
    assert valid_count == 1
    assert rejected_count == 5
    reasons = [row.reason_code for row in session.query(RejectedRow).filter_by(batch_id=batch.id).order_by(RejectedRow.line_number).all()]
    assert reasons == ["INVALID_SCORE", "OUT_OF_RANGE", "OUT_OF_RANGE", "MISSING_FIELD", "DUPLICATE_ROW"]

    score = session.query(Score).filter_by(batch_id=batch.id).one()
    assert score.student_id == "S1"
    assert score.marker_id == "M1"
    assert score.subject == "Mathematics"
    assert score.raw_score == Decimal("50.25")


def test_exact_boundary_scores_are_accepted(tmp_path):
    session = make_session(tmp_path)
    content = HEADER + b"S0,M1,Mathematics,0\nS100,M1,Mathematics,100\n"

    batch, _, valid_count, rejected_count = validate_ingest_file(session, content, "bounds.csv")

    assert valid_count == 2
    assert rejected_count == 0
    scores = [row.raw_score for row in session.query(Score).filter_by(batch_id=batch.id).all()]
    assert Decimal("0") in scores
    assert Decimal("100") in scores


def test_more_than_two_decimal_places_is_invalid_score(tmp_path):
    session = make_session(tmp_path)
    content = HEADER + b"S1,M1,Science,50.001\n"

    with pytest.raises(ValueError, match="no valid rows"):
        validate_ingest_file(session, content, "decimal.csv")

    assert session.query(Batch).count() == 0
    assert session.query(RejectedRow).count() == 0


def test_same_file_twice_returns_existing_batch_without_duplicates(tmp_path):
    session = make_session(tmp_path)
    content = HEADER + b"S1,M1,Science,50\nS2,M1,Science,60\n"

    first, duplicate1, _, _ = validate_ingest_file(session, content, "same.csv")
    second, duplicate2, _, _ = validate_ingest_file(session, content, "same-again.csv")

    assert duplicate1 is False
    assert duplicate2 is True
    assert first.id == second.id
    assert session.query(Score).filter_by(batch_id=first.id).count() == 2
    assert session.query(Batch).count() == 1
    duplicate_events = session.query(AuditEvent).filter_by(event_type="DUPLICATE_UPLOAD").count()
    assert duplicate_events == 1


def test_wrong_header_refuses_whole_file(tmp_path):
    session = make_session(tmp_path)

    with pytest.raises(ValueError, match="Wrong CSV header"):
        validate_ingest_file(session, b"student,marker,subject,raw_score\nS1,M1,X,50\n", "bad.csv")

    assert session.query(Batch).count() == 0


def test_no_valid_rows_refuses_whole_file(tmp_path):
    session = make_session(tmp_path)
    content = HEADER + b"S1,M1,Science,nope\nS2,M1,Science,-2\n"

    with pytest.raises(ValueError, match="no valid rows"):
        validate_ingest_file(session, content, "invalid.csv")

    assert session.query(Batch).count() == 0
    assert session.query(RejectedRow).count() == 0
