# Naming Taxonomy

> Canonical naming rules for ontology-driven refactors. The glossary records current collisions; this file defines target names for new code and staged migrations.

## Concept Layers

| Layer | Meaning | Canonical Naming |
|---|---|---|
| Factor | Single cross-sectional formula or formula component | `*_factor`, `*_component`, `*_score` |
| Signal | Backtest-engine input built from factor values or scheduled weights | `factor_to_signal`, `weights_to_signal` |
| Timing/Regime | Market-state label or exposure series | `*_timing_state`, `*_exposure_signal`, `*_regime_label` |
| Strategy | Executable selection/rebalance behavior | `build_*_target_weights`, `latest_decision` |
| Policy | Hard candidate/position constraint that does not claim standalone alpha | `*_filter`, `*_gate`, `*_constraint` |
| Portfolio | Multi-strategy return or weight composition | `compose_portfolio_*`, `*_portfolio` |
| Engine | Backtest, metrics, and low-level computation | engine-specific descriptive nouns |

## Required Disambiguations

- Use `zscore_cross_section` for row-wise date-by-date stock standardization.
- Use `zscore_series` for one-dimensional Series standardization.
- Use `factor_to_signal` for wrapping a factor panel as `core.engine.Signal`.
- Use `latest_decision` for a strategy's latest tradable decision; keep `latest_signal` only as a compatibility wrapper.
- Use `loser_reversal_filter` for the policy-layer death-bucket exclusion score.
- Use `salience_covariance_score` for the illiquidity/salience factor component.
- Use `small_cap_exposure_signal` for the PureTrend small-cap exposure series.

## Compatibility Policy

1. Introduce canonical names first.
2. Keep old names as wrappers until all production and tested research callers migrate.
3. Tests must prove old and new names are equivalent before callers are switched.
4. Registry strings and historical evidence are not renamed without a separate migration ADR.
5. New code must use canonical names.
