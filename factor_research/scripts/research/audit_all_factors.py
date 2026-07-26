"""用 research_toolkit.Alpha Audit 审全部在册因子(leave-one-out)。

每个因子 vs 其余全部的真增量 + 四判决:REAL=移除则丢失其余补不上的增量(载荷因子)、
NOISE=与其余冗余、TRUE_BUT_SMALL=统计真但经济上太小。是把通用审计模块接到本仓库
全因子库的"独立性地图"。

Run:
    cd factor_research && python3 scripts/research/audit_all_factors.py
"""
import json
import sys
from pathlib import Path

ROOT = Path("/Users/kiki/astcok/factor_research"); sys.path.insert(0, str(ROOT))
from factors.fundamental import bp_proxy, ep_proxy, net_profit_yoy, revenue_yoy, roe
from factors.momentum import illiquidity, mom_n, vol_ratio, volatility
from factors.small_cap import small_cap_factor
from research_toolkit import Verdict, audit_factor
from services.actions.autoresearch import _load_validation_data

close, volume, amount, _ = _load_validation_data("2018-01-01")
H = 20
fwd = close.pct_change(H, fill_method=None).shift(-H)

factors = {
  "small_cap":      small_cap_factor(amount, 60),
  "illiquidity":    illiquidity(close, volume, 20),
  "momentum60":     mom_n(close, 60),
  "momentum20":     mom_n(close, 20),
  "volatility20":   volatility(close, 20),
  "volume_ratio":   vol_ratio(volume, 5, 20),
  "roe":            roe(close),
  "net_profit_yoy": net_profit_yoy(close),
  "revenue_yoy":    revenue_yoy(close),
  "bp_proxy":       bp_proxy(close),
  "ep_proxy":       ep_proxy(close),
}
print(f"审 {len(factors)} 个在册因子(leave-one-out:每个 vs 其余全部)\n")
print(f"{'因子':<16}{'判决':>14}{'真增量':>9}{'NW ICIR':>9}{'raw':>8}")
print("-"*58)
rows=[]
for name in factors:
    base = {k:v for k,v in factors.items() if k != name}
    rep = audit_factor(factors[name], fwd, base, candidate_id=name, horizon=H, n_perm=4)
    rows.append((name, rep))
rows.sort(key=lambda x: x[1].true_increment, reverse=True)
for name, rep in rows:
    print(f"{name:<16}{rep.verdict.value.upper():>14}{rep.true_increment:>+9.4f}{rep.nw_icir:>+9.3f}{rep.raw_icir:>+8.3f}")
real = [n for n,r in rows if r.verdict==Verdict.REAL]
print("\n== 汇总 ==")
print(f"  REAL(对其余因子有真增量): {real or '无'}")
print(f"  NOISE/冗余: {[n for n,r in rows if r.verdict==Verdict.NOISE]}")
(ROOT/"reports"/"research"/"audit_all_factors.json").write_text(
    json.dumps({n:r.to_dict() for n,r in rows}, ensure_ascii=False, indent=2), encoding="utf-8")
