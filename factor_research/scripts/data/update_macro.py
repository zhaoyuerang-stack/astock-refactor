"""宏观时序层回填(市场级,无 ts_code,单时序)→ data_lake/macro/。

与股票面板(date×code)不同:宏观是 1 行/期的单时序。两形态:
  monthly  cn_cpi/cn_ppi/cn_m,按 month(YYYYMM 参考月);发布滞后,防未来在 loader 处理
  daily    shibor/moneyflow_hsgt,按 date

token 走环境变量 TUSHARE_TOKEN。量小(月度 ~300 期、日度 ~4000 天),单接口一两次调用拿全史。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from lake.sources.tushare import call  # noqa: E402

LAKE = Path("data_lake")

# name → (shape, date 参数, 字段)
MACRO_INTERFACES = {
    "cn_cpi":   ("monthly", "cpi"),
    "cn_ppi":   ("monthly", "ppi"),
    "cn_m":     ("monthly", "m"),
    "shibor":   ("daily", "shibor"),
    "moneyflow_hsgt": ("daily", "hsgt"),
}


def backfill_macro(name, start="2008-01-01"):
    shape, _ = MACRO_INTERFACES[name]
    out = LAKE / "macro" / f"{name}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    end = pd.Timestamp.today()
    if shape == "monthly":
        df = call(name, {"start_m": pd.Timestamp(start).strftime("%Y%m"),
                         "end_m": end.strftime("%Y%m")})
    else:  # daily —— 分段抓(单次上限),拼接
        frames = []
        for yr in range(pd.Timestamp(start).year, end.year + 1):
            d = call(name, {"start_date": f"{yr}0101", "end_date": f"{yr}1231"})
            if len(d):
                frames.append(d)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(df):
        keycol = "month" if shape == "monthly" else ("trade_date" if "trade_date" in df.columns else "date")
        df = df.drop_duplicates(keycol).sort_values(keycol)
        df.to_parquet(out, index=False)
    print(f"{name} [{shape}] 完成: {len(df)} 期 → {out}", flush=True)
    return len(df)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", choices=list(MACRO_INTERFACES))
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    names = list(MACRO_INTERFACES) if args.all else ([args.name] if args.name else [])
    if not names:
        ap.error("需 --name 或 --all")
    for n in names:
        backfill_macro(n)


if __name__ == "__main__":
    main()
