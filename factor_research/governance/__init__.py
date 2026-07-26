"""Loop Engineering 防自欺地基(见 LOOP_ENGINEERING.md §5)。

- trial_ledger: 持久化诚实 n_trials(§5.1)——搜得越多,DSR 惩罚越重。
- holdout: holdout 金库(§5.2)——永不用于搜索,晋级前唯一一次校验。
"""
from governance.alpha_overlay import split_alpha_overlay
from governance.decay import decay_check, rolling_3y_sharpe
from governance.holdout import (
    HoldoutAlreadyConsumed,
    HoldoutBreach,
    HoldoutIdentityMismatch,
    assert_search_clean,
    boundary,
    candidate_identity,
    current_data_fingerprint,
    holdout_trials,
    is_holdout,
    validate_on_holdout,
)
from governance.marginal import marginal_alpha
from governance.trial_ledger import cumulative_trials, honest_n_trials, record_trials

__all__ = [
    "record_trials", "cumulative_trials", "honest_n_trials",
    "boundary", "is_holdout", "assert_search_clean", "validate_on_holdout", "holdout_trials",
    "current_data_fingerprint", "candidate_identity",
    "HoldoutBreach", "HoldoutAlreadyConsumed", "HoldoutIdentityMismatch",
    "marginal_alpha", "decay_check", "rolling_3y_sharpe", "split_alpha_overlay",
]
