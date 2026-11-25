import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "rules" / "rules_v1.json"


class GradeBand(BaseModel):
    grade: str
    min: int


class Rules(BaseModel):
    version: str
    max_score: int = Field(gt=0)
    min_scripts_per_marker: int = Field(gt=0)
    deviation_threshold: float = Field(gt=0)
    max_adjustment: float = Field(gt=0)
    min_stdev: float = Field(gt=0)
    grade_bands: list[GradeBand]

    @model_validator(mode="after")
    def validate_grade_bands(self) -> "Rules":
        if not self.grade_bands:
            raise ValueError("grade_bands must not be empty")

        thresholds = [band.min for band in self.grade_bands]
        if thresholds != sorted(thresholds, reverse=True):
            raise ValueError("grade bands must be sorted in descending minimum-score order")
        if thresholds[-1] != 0:
            raise ValueError("grade bands must cover a minimum score of 0")
        if max(thresholds) > self.max_score:
            raise ValueError("a grade band minimum exceeds max_score")
        if len(set(thresholds)) != len(thresholds):
            raise ValueError("grade band minimum scores must be unique")
        return self


def load_rules(path: str | Path = DEFAULT_RULES_PATH) -> tuple[Rules, str]:
    rules_path = Path(path)
    try:
        raw_bytes = rules_path.read_bytes()
    except FileNotFoundError:
        raise ValueError(f"Rules file not found: {rules_path}")
    except OSError as exc:
        raise ValueError(f"Could not read rules file: {rules_path}: {exc}")

    try:
        data: Any = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid rules JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}")

    try:
        rules = Rules.model_validate(data)
    except Exception as exc:
        raise ValueError(f"Invalid rules file: {exc}")

    return rules, hashlib.sha256(raw_bytes).hexdigest()
