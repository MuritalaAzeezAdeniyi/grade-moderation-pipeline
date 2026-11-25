from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Iterable, Mapping, Any


FOUR_PLACES = Decimal("0.0001")
ONE_PLACE = Decimal("1")


def round_half_up(value: Decimal | int | str, places: int = 0) -> Decimal:
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    quantum = Decimal("1") if places == 0 else Decimal(1).scaleb(-places)
    return decimal_value.quantize(quantum, rounding=ROUND_HALF_UP)


def _bands(bands: Iterable[Any] | Mapping[str, Any]) -> list[Any]:
    if isinstance(bands, Mapping):
        return list(bands.get("grade_bands", []))
    return list(bands)


def grade_for(score: Decimal | int | str, bands: Iterable[Any] | Mapping[str, Any]) -> str:
    rounded = int(round_half_up(score, 0))
    for band in _bands(bands):
        minimum = band.min if hasattr(band, "min") else band["min"]
        grade = band.grade if hasattr(band, "grade") else band["grade"]
        if rounded >= int(minimum):
            return str(grade)
    raise ValueError("Grade bands do not cover the rounded score")


def _record_value(record: Any, name: str):
    return getattr(record, name) if hasattr(record, name) else record[name]


def _rule_value(rules: Any, name: str):
    if hasattr(rules, name):
        return getattr(rules, name)
    return rules[name]


def compute_stats(scores: Iterable[Any], rules: Any) -> list[dict[str, Any]]:
    """Return deterministic marker statistics grouped by subject and marker."""
    records = list(scores)
    if not records:
        return []

    subject_records: dict[str, list[Any]] = {}
    for record in records:
        subject = str(_record_value(record, "subject"))
        subject_records.setdefault(subject, []).append(record)

    stats: list[dict[str, Any]] = []
    for subject in sorted(subject_records):
        subject_scores = subject_records[subject]
        subject_values = [Decimal(str(_record_value(record, "raw_score"))) for record in subject_scores]
        cohort_mean = round_half_up(
            sum(subject_values, Decimal("0")) / Decimal(len(subject_values)), 4
        )

        marker_records: dict[str, list[Any]] = {}
        for record in subject_scores:
            marker = str(_record_value(record, "marker_id"))
            marker_records.setdefault(marker, []).append(record)

        for marker in sorted(marker_records):
            marker_scores = marker_records[marker]
            values = [Decimal(str(_record_value(record, "raw_score"))) for record in marker_scores]
            n = len(values)
            raw_marker_mean = sum(values, Decimal("0")) / Decimal(n)
            mean = round_half_up(raw_marker_mean, 4)

            variance = sum((value - raw_marker_mean) ** 2 for value in values) / Decimal(n)
            with localcontext() as context:
                context.prec = 28
                stdev = round_half_up(variance.sqrt(), 4)

            deviation = round_half_up(mean - cohort_mean, 4)
            stats.append({
                "marker_id": marker,
                "subject": subject,
                "n": n,
                "mean": mean,
                "stdev": stdev,
                "cohort_mean": cohort_mean,
                "deviation": deviation,
            })

    return stats


def compute_adjustments(scores: Iterable[Any], stats: Iterable[dict[str, Any]], rules: Any) -> list[dict[str, Any]]:
    """Compute proposed adjustment rows without database or file access."""
    records = list(scores)
    stats_by_key = {(stat["subject"], stat["marker_id"]): stat for stat in stats}

    threshold = round_half_up(_rule_value(rules, "deviation_threshold"), 4)
    max_adjustment = round_half_up(_rule_value(rules, "max_adjustment"), 0)
    max_score = Decimal(str(_rule_value(rules, "max_score")))
    minimum_n = int(_rule_value(rules, "min_scripts_per_marker"))
    bands = _rule_value(rules, "grade_bands")

    adjustments: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: (
        str(_record_value(item, "subject")), str(_record_value(item, "student_id"))
    )):
        subject = str(_record_value(record, "subject"))
        marker = str(_record_value(record, "marker_id"))
        stat = stats_by_key[(subject, marker)]

        if stat["n"] < minimum_n:
            continue

        deviation = stat["deviation"]
        if abs(deviation) <= threshold:
            continue

        clamped = max(-max_adjustment, min(max_adjustment, deviation))
        delta = int(round_half_up(-clamped, 0))
        raw_score = Decimal(str(_record_value(record, "raw_score")))
        adjusted = int(round_half_up(max(Decimal("0"), min(max_score, raw_score + Decimal(delta))), 0))

        facts = {
            "marker_mean": str(stat["mean"]),
            "cohort_mean": str(stat["cohort_mean"]),
            "deviation": str(deviation),
            "threshold": str(threshold),
            "n": stat["n"],
        }
        adjustments.append({
            "student_id": str(_record_value(record, "student_id")),
            "marker_id": marker,
            "subject": subject,
            "raw_score": raw_score,
            "delta": delta,
            "adjusted_score": adjusted,
            "grade_before": grade_for(raw_score, bands),
            "grade_after": grade_for(Decimal(adjusted), bands),
            "rule_id": "MARKER_BIAS_V1",
            "facts": facts,
            "status": "PROPOSED",
        })

    return adjustments
