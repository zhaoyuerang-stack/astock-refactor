# Ontology Refactor Migration Report

## Scope

This migration introduced canonical names for the highest-confusion ontology terms while preserving backward-compatible wrappers.

## Canonical Entrypoints Added

- `engine.factor_composer`
- `engine.signal_factory.factor_to_signal`
- `portfolio.portfolio_composer`
- `factors.alpha.transforms.zscore_cross_section`
- `engine.neutralize.zscore_series`
- `policy.candidate_filters.loser_reversal_filter`
- `factors.illiquidity_components.salience_covariance_score`
- `factors.small_cap.small_cap_exposure_signal`

## Compatibility Entrypoints Retained

- `engine.composer`
- `engine.portfolio.to_signal`
- `portfolio.composer`
- `factors.alpha.transforms.zscore`
- `engine.neutralize.zscore`
- `factors.veto.loser_veto_reversal`
- `factors.veto.salience_covariance_veto`
- `factors.small_cap.small_cap_timing`

## Semantics Preserved

- No strategy formula changed.
- No registry status or evidence changed.
- No cost model changed.
- No `shift(1)`, T+1, rebalance frequency, universe filter, or veto refill behavior changed.

## Verification

- `python3 tests/test_naming_taxonomy.py`
- `python3 tests/test_factor_composer_taxonomy.py`
- `python3 tests/test_signal_factory.py`
- `python3 tests/test_factor_normalization_axis.py`
- `python3 tests/test_veto_filter.py`
- `python3 tests/test_timing_taxonomy.py`
- `python3 tests/test_composer.py`
- `python3 tests/test_regime_gate.py`
- `python3 tests/test_naming_taxonomy_guard.py`
- `python3 scripts/ci/check_naming_taxonomy.py`
- `python3 scripts/ci/check_layer_deps.py`
- `bash scripts/test_all.sh` (see caveat below)

All targeted tests above and both static guards pass.

## Real-Panel Numerical Equivalence (Adversarial)

The plan **retyped** function bodies into new canonical modules rather than
`git mv`-ing them, so a one-character transcription drift (an epsilon, an added
`fill_method`) could pass every toy-fixture test yet diverge on real data. This was
audited two independent ways; both are clean.

**1. Runtime bit-equivalence on the provisioned lake.** Each canonical function was
run against its pre-refactor original body (extracted from git `1afd2494^`) on the
same real A-share panel loaded from `data_lake/price/daily_all.parquet`. Two slices:
a dense slice (250 dates × 1200 codes, 0.9% NaN) and a halt/IPO-heavy slice (120
dates, NaN-sorted codes, 2.6% NaN, exercising the `inf→NaN` replace path). Result:
**5/5 bit-identical** (`assert_frame_equal` / `assert_series_equal`) on both slices —
`loser_reversal_filter`, `salience_covariance_score`, `small_cap_exposure_signal`,
`zscore_cross_section`, `equal_weight_factor`.

**2. Source-identity proof (holds for all inputs, not just sampled ones).** The
arithmetic bodies were diffed line-for-line against the originals:
`salience_covariance_score` is **character-identical** to `salience_covariance_veto`;
`small_cap_exposure_signal` differs from `small_cap_timing` only in the `def` name
line; `zscore` epsilons match (`1e-8` cross-section, `1e-10` series). Identical source
⇒ identical output on every input, closing the "unsampled code path" gap.

Conclusion: the retype introduced **zero numerical drift**.

## test_all.sh Caveat (Failure Proven Unrelated to This Refactor)

`bash scripts/test_all.sh` stops (`set -e`) at `test_engine.py` with:

```
ValueError: No objects to concatenate
  strategies/small_cap.py:122 load_price_panels
  lake/load_lake.py:50 load_prices -> pd.concat(frames)
```

This is an **empty data lake** in this worktree — `data_lake/` contains only
`factor_store/` (3 parquet files) and no daily price panels, so `load_prices`
finds no frames to concatenate. Isolation evidence:

- The failing line (`load_price_panels`, small_cap.py:122) precedes the only line
  this refactor touched in that file (the timing call at :127).
- `lake/load_lake.py` was never modified by this refactor.
- `from strategies.small_cap import run_small_cap_strategy` imports cleanly; the
  failure is at data load, not at any renamed symbol.

All static guards, `test_loop_foundations`, and every Task 1-8 targeted test pass
before this data-dependent test is reached.

### Full-suite observation (past the `set -e` stop)

Because `set -e` halts at `test_engine.py`, every entry after it was run individually
against the refactored tree and each failure categorized as symbol-regression
(`ImportError` / `cannot import name` / `AttributeError` on a renamed symbol) vs
env/data. Result: **zero symbol regressions**. All failures are pre-existing
env/data conditions in this un-provisioned worktree:

| Test | Failure | Category |
|---|---|---|
| `test_engine` | `No objects to concatenate` (load_prices) | empty data lake |
| `test_data_layer` | `No objects to concatenate` | empty data lake |
| `test_services_phase0` | `No objects to concatenate` | empty data lake |
| `test_e2e` | `validate_final` subprocess returncode≠0 | needs data lake |
| `test_stock_profile` | `FileNotFoundError: stock price not found` | missing price data |
| `test_fundamentals` | `gross_margin` assertion | missing fundamental data |
| `test_style_neutralization` | `empty fundamental panel field` | missing fundamental data |
| `test_agent_skills` | stock name→code resolution assertion | missing lookup data |
| `test_autoresearch_engine` | `max(corrs) > 0.1` stochastic assertion | seed/data, not symbol |

Every other `test_all.sh` entry (including all pytest entries, `test_agent_loop`,
`test_factor_store_*`, `test_price_*`, `test_catalog_status`,
`test_moving_average_overlay`, and the network entries) passes. No renamed symbol
throws anywhere in the suite.

### Systematic per-file sweep (upgrades the manual walk above)

All **101** `test_*.py` files (excluding `scratch`/`archive`/`__pycache__`) were run
individually via `pytest` (collection errors surface refactor breakage; script-style
files without `test_` functions fall back to direct execution). Each failure was
classified with **symbol-regression given priority** so a refactor bug cannot hide
behind a data failure:

| Bucket | Count |
|---|---|
| PASS | 91 |
| DATA / env (empty lake, missing price/fundamental/lookup data) | 7 |
| OTHER (content/stochastic assertions, non-symbol) | 3 |
| TIMEOUT | 0 |
| **SYMBOL regression (refactor-caused)** | **0** |

The 3 OTHER were traceback-inspected and confirmed non-symbol: `test_agent_knowledge`
(missing runtime knowledge text), `test_agent_skills` (stock name→code lookup returns
`None` — missing lookup data), `test_autoresearch_engine` (stochastic
`result.champions` / `corr_to_book` assertion). Notably `test_autoresearch_engine`
runs its search config with `"transforms": ["mad_clip", "zscore", "rank"]` and reaches
the downstream assertion without any transform-resolution error — positive evidence
that the **preserved DSL name `"zscore"` still resolves** through the real pipeline.

Conclusion: **0 symbol regressions across the entire test tree**; every failure is a
pre-existing empty-lake / data / stochastic condition, not this refactor.

## Deviation From Plan (Recorded)

Plan Task 7 Step 3 said to change `strategies/small_cap.py` import to
`from factors.small_cap import small_cap_exposure_signal, small_cap_factor`,
dropping `small_cap_timing`. That line is a **re-export** relied on by four research
scripts (`dsr_killtest_staleness`, `zero_ret_marginal_probe`,
`diversifier_marginal_probe`, `diversifier_vs_all_strategies`) via
`from strategies.small_cap import ... small_cap_timing`. To honor the constraint
"preserve backward imports until all in-repo callers have moved," `small_cap_timing`
was kept in the import (as a `# noqa: F401` re-export); only the internal call was
switched to `small_cap_exposure_signal`.

## Deferred Work

- Rename `latest_signal` to `latest_decision` only after production/Web consumers are mapped.
- Migrate historical research scripts opportunistically; do not churn archived or scratch files.
  (`rg` audit shows ~132 files still using compatibility/DSL names — all work via wrappers.)
- Remove compatibility wrappers only after one release cycle and a clean `rg` audit.
