import hashlib

import pytest

from app.rules import load_rules


def test_rules_load_and_hash_are_stable():
    rules, rules_hash = load_rules()

    rules_again, rules_hash_again = load_rules()

    assert rules.version == "v1"
    assert rules.max_score == 100
    assert rules_hash == rules_hash_again
    assert len(rules_hash) == 64


def test_rules_hash_matches_file_bytes():
    from app.rules import DEFAULT_RULES_PATH

    _, rules_hash = load_rules()

    assert rules_hash == hashlib.sha256(DEFAULT_RULES_PATH.read_bytes()).hexdigest()


@pytest.mark.parametrize("content, message", [
    ('{"version":"v1","max_score":0}', "Invalid rules file"),
    ('{"version":"v1","max_score":100,"min_scripts_per_marker":20,"deviation_threshold":5,"max_adjustment":8,"min_stdev":3,"grade_bands":[{"grade":"A","min":70},{"grade":"F","min":10}]}', "cover a minimum score of 0"),
    ('{"version":"v1","max_score":100,"min_scripts_per_marker":20,"deviation_threshold":5,"max_adjustment":8,"min_stdev":3,"grade_bands":[{"grade":"F","min":0},{"grade":"A","min":70}]}', "descending minimum-score order"),
    ('not-json', "Invalid rules JSON"),
])
def test_invalid_rules_files_are_rejected(tmp_path, content, message):
    path = tmp_path / "bad_rules.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_rules(path)
