# Research Toolkit

This folder is the standalone strategy research and control-rule validation
toolkit. It is intentionally narrower than a generic quant platform.

## Scope

- Model host-scoped control artifacts such as `VetoFilter`.
- Apply policy rules to a host candidate pool without changing exposure.
- Evaluate marginal contribution against a host with delta-only reports.
- Triage failed factory candidates into control-rule review branches.
- **Alpha Audit**: a host/market-agnostic factor lie-detector — given a factor's
  daily panel + a known-factor pool, return an honest marginal verdict.

## Alpha Audit

The audit's value is *rejecting* fake alpha, not finding it. `audit_factor`
takes `(candidate_panel, forward_returns, base_panels)` — all `(T,N)` panels the
caller supplies — and returns one of four verdicts:

| Verdict | Meaning | Action |
|---|---|---|
| `REAL` | true increment ≥ economic threshold, statistically significant | new alpha |
| `TRUE_BUT_SMALL` | significant but below economic threshold | archive, "no investable value" |
| `NOISE` | indistinguishable from permutation (redundant / price-in) | discard |
| `UNDECIDABLE` | sample too sparse to fit | change frequency / get more data |

Weapons (all "reject" mechanisms, ported then locally recomputed):
- **NW overlap correction** — horizon>1 daily-IC autocorrelation inflates raw
  ICIR ~3.5×; `nw_icir` gives the honest magnitude.
- **RidgeCV joint increment + permutation** — `true_inc = surface_inc − permuted_inc`;
  permutation preserves the NaN pattern and destroys predictive power, isolating
  structure/redundancy from genuine increment.

Pure: no data loading, no `data_lake`/`factors` dependency — any market, any
factor library plugs in. Real-data demo: `scripts/research/alpha_audit_fund_mom.py`.

## Boundaries

- A control artifact is not an independent strategy.
- Veto-style artifacts must not publish standalone NAV or annualized returns.
- Exposure reduction, timing overlays, and risk controls are separate artifact
  types and must not be mixed into candidate-pool veto attribution.

## Typical Flow

1. Create a `ControlArtifact` with a `HostSpec`.
2. Apply a policy transform such as `apply_veto_filter` before host `top_n`.
3. Compare base host vs controlled host through `compute_marginal_report`.
4. Register only as `条件假设/观察` until real OOS evidence accumulates.
