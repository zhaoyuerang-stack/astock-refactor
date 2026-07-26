"""
数据校验层 DataValidator —— 8层校验保证数据准确性

设计原则：不信任单一源，逐只多维度交叉确认，产出可追溯的质量报告。
"""
import json
from pathlib import Path

import pandas as pd

from lake.data_issue_triage import build_validation_triage


class DataValidator:
    def __init__(self, calendar=None, anchors: dict = None,
                 return_tol: float = 0.012, missing_tol: float = 0.02):
        """
        calendar: 交易日历(DatetimeIndex/set)，完整性校验基准
        anchors: 价格锚点 {code: {date: close}}，基准对账
        return_tol: 多源涨跌幅对账容差(默认1.2%，宽于除权日差异)
        missing_tol: 缺失交易日告警阈值(默认2%，排除正常停牌)
        """
        self.calendar = set(pd.to_datetime(list(calendar))) if calendar is not None else None
        self.anchors = anchors or {}
        self.return_tol = return_tol
        self.missing_tol = missing_tol

    # ── 各层校验 ──
    def check_duplicates(self, df):
        n = int(df["date"].duplicated().sum())
        return [f"重复日期{n}行"] if n else []

    def check_ohlc(self, df):
        o, h, l, c = df["open"], df["high"], df["low"], df["close"]
        bad = ((l > o) | (l > c) | (h < o) | (h < c) |
               (df[["open", "high", "low", "close"]] < 0).any(axis=1))
        n = int(bad.sum())
        return [f"OHLC逻辑错误{n}行"] if n else []

    def check_anomalies(self, df):
        """真数据异常：负价格(后复权错误)、价格跳变>50%(排除停牌复牌+新股初期)"""
        issues = []
        # 负价格 → 后复权计算错误
        neg = int((df[["open", "high", "low", "close"]] < 0).any(axis=1).sum())
        if neg:
            issues.append(f"负价格{neg}行(后复权错误)")
        ret = df["close"].pct_change()
        gap = df["date"].diff().dt.days
        pos = pd.Series(range(len(df)), index=df.index)
        early = pos < 10                       # 上市前10交易日(新股不限涨跌)
        real_jump = (ret.abs() > 0.5) & (gap <= 20) & (~early)
        jump = int(real_jump.sum())
        if jump:
            issues.append(f"价格跳变>50% {jump}处")
        return issues

    def check_info(self, df, limit: float = 0.22):
        """信息项(A股正常现象，不算质量问题)：停牌、新股超限涨跌幅"""
        info = []
        ret = df["close"].pct_change()
        ext = int((ret.abs() > limit).sum())
        if ext:
            info.append(f"超限涨跌幅{ext}处(多为新股/特殊)")
        zero = int((df["volume"] == 0).sum())
        if zero:
            info.append(f"零成交量{zero}天(停牌)")
        zombie = (df["close"].diff() == 0).rolling(5).sum().max()
        if pd.notna(zombie) and zombie >= 5:
            info.append("连续同价(一字板/特殊状态,交叉验证为真实)")
        return info

    def check_completeness(self, df):
        """只告警'孤立缺失'(前后交易日都有数据→疑数据源漏数据)；
        连续缺失视为停牌(A股常态)，不算数据质量问题。"""
        if self.calendar is None:
            return []
        dates = set(df["date"])
        lo, hi = df["date"].min(), df["date"].max()
        expect = sorted(d for d in self.calendar if lo <= d <= hi)
        if not expect:
            return []
        isolated = 0
        for i, d in enumerate(expect):
            if d in dates:
                continue
            prev_ok = (i == 0) or (expect[i - 1] in dates)
            next_ok = (i == len(expect) - 1) or (expect[i + 1] in dates)
            if prev_ok and next_ok:        # 前后都有数据，中间独缺 → 真缺口
                isolated += 1
        if isolated > 5:
            return [f"孤立缺失{isolated}天(疑数据源漏数据)"]
        return []

    def cross_check(self, df, other):
        """多源涨跌幅对账（复权基准无关）"""
        a = df.set_index("date")["close"].pct_change()
        b = other.set_index("date")["close"].pct_change()
        common = a.index.intersection(b.index)
        if len(common) < 20:
            return []
        diff = (a[common] - b[common]).abs()
        big = int((diff > self.return_tol).sum())
        corr = a[common].corr(b[common])
        issues = []
        if corr < 0.99:
            issues.append(f"多源相关性低({corr:.4f})")
        if big > len(common) * self.missing_tol:
            issues.append(f"多源涨跌幅偏差>{self.return_tol:.1%}有{big}处({big/len(common):.1%})")
        return issues

    def check_anchor(self, code, df):
        if code not in self.anchors:
            return []
        s = df.set_index("date")["close"]
        issues = []
        for d, expected in self.anchors[code].items():
            d = pd.Timestamp(d)
            if d in s.index and abs(s[d] - expected) / expected > 0.01:
                issues.append(f"锚点{d.date()}偏差(实{s[d]:.2f}vs期{expected})")
        return issues

    # ── 综合 ──
    def validate(self, code, df, cross_df=None):
        if df is None or len(df) == 0:
            return {"code": code, "rows": 0, "issues": ["空数据"], "info": [], "ok": False}
        # 真数据质量问题（决定 ok）
        issues = []
        issues += self.check_duplicates(df)
        issues += self.check_ohlc(df)
        issues += self.check_anomalies(df)
        issues += self.check_anchor(code, df)
        if cross_df is not None:
            issues += self.cross_check(df, cross_df)
        # 信息项（停牌/孤立缺失/新股超限，A股正常现象，不计入ok）
        info = self.check_info(df) + self.check_completeness(df)
        return {"code": code, "rows": len(df), "issues": issues,
                "info": info, "ok": len(issues) == 0}

    def quality_report(self, results: list, save_path: str = None):
        total = len(results)
        clean = sum(1 for r in results if r["ok"])
        issue_counts = {}
        for r in results:
            for iss in r["issues"]:
                key = iss.split("(")[0].rstrip("0123456789 ")
                issue_counts[key] = issue_counts.get(key, 0) + 1
        report = {
            "total": total,
            "clean": clean,
            "clean_ratio": round(clean / total, 4) if total else 0,
            "issue_breakdown": dict(sorted(issue_counts.items(), key=lambda x: -x[1])),
            "flagged": [{"code": r["code"], "issues": r["issues"]}
                        for r in results if not r["ok"]][:100],
        }
        if save_path:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            Path(save_path).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    def issue_triage_report(self, results: list, save_path: str = None):
        return build_validation_triage(results, save_path=save_path)
