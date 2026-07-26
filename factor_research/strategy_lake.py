"""当前生产策略 data_lake 口径复测。

统一使用 services/actions/run_backtest.py 中的 illiquidity/v3.1 构建路径,
避免 Web/API 和 CLI 的"生产回测"口径漂移。
"""
import warnings; warnings.filterwarnings("ignore")
import os
from pathlib import Path

os.chdir(Path(__file__).parent)

from services.actions.run_backtest import run_production_engine_backtest


def run_backtest(label, start):
    print(f"\n{label}", flush=True)
    result, close = run_production_engine_backtest(start=start)

    # Output (identical format to legacy)
    m = result.metrics
    print(f"  策略 {result.family}/{result.version}", flush=True)
    print(f"  载入 {close.shape[1]}只 × {close.shape[0]}日 "
          f"[{close.index[0].date()}~{close.index[-1].date()}]", flush=True)
    print(f"  → 年化={m['annual']:+.2%} 回撤={m['maxdd']:.2%} 夏普={m['sharpe']:.2f} "
          f"卡玛={m['calmar']:.2f} 达标={'✅' if m['hit'] else '❌'}", flush=True)
    print(f"  成本: 年均换手={result.detail['turnover'].mean()*252:.1f}x "
          f"年均成本拖累={result.detail['cost'].mean()*252:.2%}", flush=True)
    yearly = result.yearly_returns
    print("  分年度:", " ".join(f"{y}:{r:+.0%}" for y, r in yearly.items()), flush=True)
    return result


def main():
    for label, start in [
        ("① 2018-2026 (真实成本·data_lake)", "2018-01-01"),
        ("② 2010-2026 (压力测试·含2015股灾/2017小盘崩盘)", "2010-01-01"),
    ]:
        run_backtest(label, start)


if __name__ == "__main__":
    main()
