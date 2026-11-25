import csv
import random
from pathlib import Path


SEED = 2026
OUTPUT = Path(__file__).resolve().parent.parent / "sample" / "scores_sample.csv"
HEADER = ["student_id", "marker_id", "subject", "raw_score"]


def make_marker_scores(rng: random.Random, marker: str, subject: str,
                       count: int, mean: int, spread: int,
                       start_index: int) -> list[list[str]]:
    rows = []
    for offset in range(count):
        score = max(0, min(100, round(rng.gauss(mean, spread))))
        student_number = start_index + offset
        rows.append([f"{subject[:3].upper()}-{student_number:04d}", marker, subject, str(score)])
    return rows


def main() -> None:
    rng = random.Random(SEED)
    rows: list[list[str]] = []

    for subject in ("Mathematics", "Science"):
        subject_start = 1 if subject == "Mathematics" else 200
        start = subject_start

        marker_specs = (
            ("STRICT", 53, 5),
            ("GENEROUS", 72, 5),
            ("NORMAL_A", 65, 6),
            ("NORMAL_B", 66, 6),
            ("LOW_SPREAD", 65, 1),
        )
        for marker, mean, spread in marker_specs:
            marker_rows = make_marker_scores(rng, marker, subject, 25, mean, spread, start)
            rows.extend(marker_rows)
            start += 25

        rows.extend(make_marker_scores(rng, "SMALL", subject, 8, 63, 7, start))

    # Explicit bad rows exercise all ingestion rejection codes.
    rows.extend([
        ["", "NORMAL_A", "Mathematics", "50"],
        ["BAD-NONNUMERIC", "NORMAL_A", "Science", "not-a-score"],
        ["BAD-NEGATIVE", "NORMAL_A", "Science", "-1"],
        ["BAD-OVER", "NORMAL_A", "Science", "101"],
        [rows[0][0], rows[0][1], rows[0][2], "55"],
        ["BAD-MISSING-SCORE", "NORMAL_A", "Science", ""],
    ])

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)


if __name__ == "__main__":
    main()
