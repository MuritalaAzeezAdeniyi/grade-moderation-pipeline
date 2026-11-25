from dataclasses import dataclass
from decimal import Decimal

from app.alerts import build_alerts


@dataclass(frozen=True)
class Rules:
    min_scripts_per_marker: int = 20
    min_stdev: Decimal = Decimal("3")
    max_adjustment: Decimal = Decimal("8")


def test_all_alert_types_are_built():
    stats = [{
        "marker_id": "M1", "subject": "Math", "n": 20,
        "mean": Decimal("80.0000"), "stdev": Decimal("2.0000"),
        "cohort_mean": Decimal("50.0000"), "deviation": Decimal("30.0000"),
    }]
    adjustments = [
        {"marker_id": "M1", "subject": "Math", "raw_score": Decimal("80"),
         "adjusted_score": 72, "grade_before": "A", "grade_after": "B"}
    ]

    alerts = build_alerts(stats, adjustments, Rules())

    assert [alert["alert_type"] for alert in alerts] == [
        "MARKER_DEVIATION", "ADJUSTMENT_CAPPED", "LOW_SPREAD", "GRADE_CHANGED"
    ]


def test_insufficient_sample_alert():
    stats = [{
        "marker_id": "SMALL", "subject": "Science", "n": 19,
        "mean": Decimal("60.0000"), "stdev": Decimal("2.0000"),
        "cohort_mean": Decimal("55.0000"), "deviation": Decimal("5.0000"),
    }]

    alerts = build_alerts(stats, [], Rules())

    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "INSUFFICIENT_SAMPLE"
    assert alerts[0]["severity"] == "info"


def test_stdev_exactly_at_minimum_is_not_low_spread():
    stats = [{
        "marker_id": "NORMAL", "subject": "Math", "n": 20,
        "mean": Decimal("50.0000"), "stdev": Decimal("3.0000"),
        "cohort_mean": Decimal("50.0000"), "deviation": Decimal("0.0000"),
    }]

    assert build_alerts(stats, [], Rules()) == []


def test_n_exactly_at_minimum_is_moderated_boundary():
    stats = [{
        "marker_id": "BOUNDARY", "subject": "Math", "n": 20,
        "mean": Decimal("60.0000"), "stdev": Decimal("4.0000"),
        "cohort_mean": Decimal("50.0000"), "deviation": Decimal("10.0000"),
    }]
    adjustments = [{
        "marker_id": "BOUNDARY", "subject": "Math", "raw_score": Decimal("60"),
        "adjusted_score": 52, "grade_before": "A", "grade_after": "C",
    }]

    alerts = build_alerts(stats, adjustments, Rules())

    types = [alert["alert_type"] for alert in alerts]
    assert "INSUFFICIENT_SAMPLE" not in types
    assert "MARKER_DEVIATION" in types


def test_deterministic_order_across_subject_marker_and_alert_type():
    stats = [
        {"marker_id": "Z", "subject": "Science", "n": 20, "mean": Decimal("80"),
         "stdev": Decimal("2"), "cohort_mean": Decimal("50"), "deviation": Decimal("30")},
        {"marker_id": "A", "subject": "Math", "n": 20, "mean": Decimal("80"),
         "stdev": Decimal("2"), "cohort_mean": Decimal("50"), "deviation": Decimal("30")},
    ]
    adjustments = [
        {"marker_id": "Z", "subject": "Science", "grade_before": "A", "grade_after": "B"},
        {"marker_id": "A", "subject": "Math", "grade_before": "A", "grade_after": "B"},
    ]

    first = build_alerts(stats, adjustments, Rules())
    second = build_alerts(list(reversed(stats)), list(reversed(adjustments)), Rules())

    assert first == second
    assert [(alert["subject"], alert["marker_id"])
            for alert in first] == [
        ("Math", "A"), ("Math", "A"), ("Math", "A"), ("Math", "A"),
        ("Science", "Z"), ("Science", "Z"), ("Science", "Z"), ("Science", "Z"),
    ]


def test_grade_changed_details_and_alert_do_not_change_scores():
    stats = [{
        "marker_id": "M1", "subject": "Math", "n": 20,
        "mean": Decimal("69.5000"), "stdev": Decimal("4.0000"),
        "cohort_mean": Decimal("50.0000"), "deviation": Decimal("19.5000"),
    }]
    original_scores = [Decimal("69.50"), Decimal("70.00")]
    adjustments = [
        {"marker_id": "M1", "subject": "Math", "raw_score": original_scores[0],
         "adjusted_score": 70, "grade_before": "B", "grade_after": "A"},
        {"marker_id": "M1", "subject": "Math", "raw_score": original_scores[1],
         "adjusted_score": 62, "grade_before": "A", "grade_after": "B"},
    ]

    alerts = build_alerts(stats, adjustments, Rules())
    grade_alert = next(alert for alert in alerts if alert["alert_type"] == "GRADE_CHANGED")

    assert grade_alert["details"] == {"count": 2, "percentage": "100.0000", "n": 2}
    assert [row["raw_score"] for row in adjustments] == original_scores
