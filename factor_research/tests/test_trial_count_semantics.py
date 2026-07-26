import pytest

from governance.trial_ledger import honest_n_trials, record_trials
from workflow.nine_gate_runner import TrialCountUnknown, _family_n_trials


def test_honest_trial_count_includes_failed_and_parameter_attempts(tmp_path):
    path = tmp_path / "trials.jsonl"
    record_trials("illiquidity", 8, context="failed candidates", path=path)
    record_trials("illiquidity", 2, context="retained variants", path=path)

    assert honest_n_trials("illiquidity", path=path) == 10


def test_different_data_vintage_is_a_new_trial(tmp_path):
    path = tmp_path / "trials.jsonl"
    record_trials("illiquidity", 1, context="vintage-a", spec_hash="same", path=path)
    record_trials("illiquidity", 1, context="vintage-b", spec_hash="same", path=path)

    assert honest_n_trials("illiquidity", path=path) == 2


def test_missing_trial_ledger_is_unknown_not_a_floor(tmp_path):
    with pytest.raises(TrialCountUnknown):
        _family_n_trials("illiquidity", path=tmp_path / "missing.jsonl")
