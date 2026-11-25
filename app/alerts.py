from decimal import Decimal
from typing import Any, Iterable

from app.engine import round_half_up


ALERT_ORDER = {
    "MARKER_DEVIATION": 0,
    "ADJUSTMENT_CAPPED": 1,
    "LOW_SPREAD": 2,
    "INSUFFICIENT_SAMPLE": 3,
    "GRADE_CHANGED": 4,
}


def _rule_value(rules: Any, name: str):
    return getattr(rules, name) if hasattr(rules, name) else rules[name]


def build_alerts(stats: Iterable[dict[str, Any]], adjustments: Iterable[dict[str, Any]], rules: Any) -> list[dict[str, Any]]:
    """Build deterministic human-facing alerts without database or file access."""
    stats_list = list(stats)
    adjustment_list = list(adjustments)
    minimum_n = int(_rule_value(rules, "min_scripts_per_marker"))
    min_stdev = Decimal(str(_rule_value(rules, "min_stdev")))
    max_adjustment = Decimal(str(_rule_value(rules, "max_adjustment")))

    adjustments_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for adjustment in adjustment_list:
        key = (str(adjustment["subject"]), str(adjustment["marker_id"]))
        adjustments_by_key.setdefault(key, []).append(adjustment)

    alerts: list[dict[str, Any]] = []
    for stat in sorted(stats_list, key=lambda item: (str(item["subject"]), str(item["marker_id"]))):
        marker_id = str(stat["marker_id"])
        subject = str(stat["subject"])
        key = (subject, marker_id)
        n = int(stat["n"])
        stdev = Decimal(str(stat["stdev"]))
        deviation = Decimal(str(stat["deviation"]))
        marker_adjustments = adjustments_by_key.get(key, [])

        if marker_adjustments:
            alerts.append({
                "marker_id": marker_id,
                "subject": subject,
                "alert_type": "MARKER_DEVIATION",
                "severity": "warn",
                "details": {
                    "marker_mean": str(stat["mean"]),
                    "cohort_mean": str(stat["cohort_mean"]),
                    "deviation": str(deviation),
                    "n": n,
                },
            })

        if marker_adjustments and abs(deviation) > max_adjustment:
            alerts.append({
                "marker_id": marker_id,
                "subject": subject,
                "alert_type": "ADJUSTMENT_CAPPED",
                "severity": "warn",
                "details": {
                    "deviation": str(deviation),
                    "max_adjustment": str(max_adjustment),
                },
            })

        if n >= minimum_n and stdev < min_stdev:
            alerts.append({
                "marker_id": marker_id,
                "subject": subject,
                "alert_type": "LOW_SPREAD",
                "severity": "warn",
                "details": {
                    "stdev": str(stdev),
                    "min_stdev": str(min_stdev),
                    "n": n,
                },
            })

        if n < minimum_n:
            alerts.append({
                "marker_id": marker_id,
                "subject": subject,
                "alert_type": "INSUFFICIENT_SAMPLE",
                "severity": "info",
                "details": {
                    "n": n,
                    "min_scripts_per_marker": minimum_n,
                },
            })

        changed = sum(
            1 for adjustment in marker_adjustments
            if adjustment["grade_before"] != adjustment["grade_after"]
        )
        if changed:
            percentage = round_half_up(
                Decimal(changed) * Decimal("100") / Decimal(len(marker_adjustments)), 4
            )
            alerts.append({
                "marker_id": marker_id,
                "subject": subject,
                "alert_type": "GRADE_CHANGED",
                "severity": "info",
                "details": {
                    "count": changed,
                    "percentage": str(percentage),
                    "n": len(marker_adjustments),
                },
            })

    alerts.sort(key=lambda alert: (
        str(alert["subject"]),
        str(alert["marker_id"]),
        ALERT_ORDER[alert["alert_type"]],
    ))
    return alerts


# Descriptive alias for callers that prefer the full function name.
build_alert_list = build_alerts
