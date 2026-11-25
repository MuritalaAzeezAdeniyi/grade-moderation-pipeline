from dataclasses import dataclass
from decimal import Decimal

from app.engine import compute_adjustments, compute_stats, grade_for, round_half_up


@dataclass(frozen=True)
class Score:
    student_id: str
    marker_id: str
    subject: str
    raw_score: Decimal


class Rules:
    max_score = 100
    min_scripts_per_marker = 20
    deviation_threshold = Decimal("5")
    max_adjustment = Decimal("8")
    min_stdev = Decimal("3")
    grade_bands = [
        {"grade": "A", "min": 70},
        {"grade": "B", "min": 60},
        {"grade": "C", "min": 50},
        {"grade": "D", "min": 40},
        {"grade": "F", "min": 0},
    ]


def score_rows(marker_scores: dict[str, list[int]], subject="Math"):
    rows = []
    for marker, values in marker_scores.items():
        for index, value in enumerate(values):
            rows.append(Score(f"{marker}-{index}", marker, subject, Decimal(value)))
    return rows


def test_round_half_up_and_tie():
    assert round_half_up("2.3450", 2) == Decimal("2.35")
    assert round_half_up("1.5", 0) == Decimal("2")
    assert round_half_up("-1.5", 0) == Decimal("-2")


def test_grade_band_boundary_uses_half_up():
    assert grade_for(Decimal("69.49"), Rules.grade_bands) == "B"
    assert grade_for(Decimal("69.50"), Rules.grade_bands) == "A"
    assert grade_for(Decimal("70.00"), Rules.grade_bands) == "A"


def test_deviation_exactly_at_threshold_is_not_adjusted():
    rows = score_rows({"M1": [60] * 20, "M2": [50] * 20})
    stats = compute_stats(rows, Rules())
    m1 = next(item for item in stats if item["marker_id"] == "M1")
    assert m1["deviation"] == Decimal("5.0000")
    assert compute_adjustments(rows, stats, Rules()) == []


def test_just_above_threshold_is_adjusted():
    rows = score_rows({"M1": [61] * 20, "M2": [50] * 20})
    stats = compute_stats(rows, Rules())
    adjustments = compute_adjustments(rows, stats, Rules())
    assert len(adjustments) == 40
    m1 = next(row for row in adjustments if row["marker_id"] == "M1")
    assert m1["delta"] == -6
    assert m1["adjusted_score"] == 55


def test_deviation_larger_than_max_adjustment_is_capped():
    rows = score_rows({"M1": [80] * 20, "M2": [50] * 20})
    stats = compute_stats(rows, Rules())
    adjustment = compute_adjustments(rows, stats, Rules())[0]
    assert adjustment["delta"] == -8
    assert adjustment["adjusted_score"] == 72


def test_clamping_at_zero_and_max_score():
    rows = score_rows({"LOW": [0] * 20, "HIGH": [100] * 20})
    adjustments = compute_adjustments(rows, compute_stats(rows, Rules()), Rules())

    low = [row for row in adjustments if row["marker_id"] == "LOW"]
    high = [row for row in adjustments if row["marker_id"] == "HIGH"]
    assert low[0]["delta"] == 8
    assert low[0]["adjusted_score"] == 8
    assert high[0]["delta"] == -8
    assert high[0]["adjusted_score"] == 92


def test_below_and_at_minimum_script_count():
    below = score_rows({"M1": [100] * 19, "M2": [50] * 20})
    below_stats = compute_stats(below, Rules())
    below_adjustments = compute_adjustments(below, below_stats, Rules())
    assert not any(row["marker_id"] == "M1" for row in below_adjustments)

    at = score_rows({"M1": [100] * 20, "M2": [50] * 20})
    at_stats = compute_stats(at, Rules())
    at_adjustments = compute_adjustments(at, at_stats, Rules())
    assert any(row["marker_id"] == "M1" for row in at_adjustments)
    assert len(at_adjustments) == 40


def test_subjects_are_handled_separately():
    rows = [
        Score("A1", "M1", "Math", Decimal("80")),
        Score("A2", "M1", "Math", Decimal("80")),
        Score("B1", "M1", "Science", Decimal("20")),
        Score("B2", "M1", "Science", Decimal("20")),
    ]
    rows += [
        Score(f"N{i}", "NORMAL", "Math", Decimal("60") if i < 20 else Decimal("60"))
        for i in range(20)
    ]
    rows += [
        Score(f"S{i}", "NORMAL", "Science", Decimal("40"))
        for i in range(20)
    ]

    stats = compute_stats(rows, Rules())
    math_stats = [stat for stat in stats if stat["subject"] == "Math"]
    science_stats = [stat for stat in stats if stat["subject"] == "Science"]
    assert math_stats and science_stats


def test_identical_input_is_deterministic():
    rows = score_rows({"M1": [75] * 20, "M2": [60] * 20})
    first_stats = compute_stats(rows, Rules())
    first = compute_adjustments(rows, first_stats, Rules())
    second_stats = compute_stats(rows, Rules())
    second = compute_adjustments(rows, second_stats, Rules())
    assert first == second
