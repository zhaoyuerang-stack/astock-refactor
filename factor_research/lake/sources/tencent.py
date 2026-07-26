"""腾讯后复权日线源——日线主力（JSON直出，不依赖py_mini_racer）"""
import threading
from datetime import date

import pandas as pd
import requests

from lake.base import Fetcher, RateLimiter
from lake.sources.registry import register

URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


@register("tencent_daily")
class TencentDailyFetcher(Fetcher):
    """
    腾讯 fqkline 后复权日线。单次最多640条 → 用END日期往前滚动分批，覆盖到 start。
    数组格式: [date, open, close, high, low, volume]
    """
    def __init__(self, out_dir: str = "data_lake/price/daily",
                 start: str = "2010-01-01", **kw):
        super().__init__(
            name="tencent_daily", out_dir=out_dir,
            limiter=RateLimiter(min_interval=0.3, jitter=(0.1, 0.3)),
            max_workers=kw.pop("max_workers", 6), **kw,
        )
        self.start = start
        self._local = threading.local()

    @property
    def session(self):
        if not hasattr(self._local, "s"):
            self._local.s = requests.Session()
        return self._local.s

    @staticmethod
    def to_tx(code: str):
        if code.startswith("6"):
            return "sh" + code
        if code.startswith(("0", "3")):
            return "sz" + code
        return None

    def fetch_one(self, code: str):
        sym = self.to_tx(code)
        if sym is None:
            return None
        today = date.today().isoformat()
        end = today
        seen, rows = set(), []
        for _ in range(15):                       # 最多15批(够回溯到1990s)
            param = f"{sym},day,{self.start},{end},640,hfq"
            d = self.session.get(URL, params={"param": param}, timeout=15).json()
            node = d.get("data")
            if not isinstance(node, dict):
                break
            k = node.get(sym, {})
            # 铁律:本源是后复权湖,hfqday 缺失时绝不回退 "day"(不复权)——
            # 静默混口径会把不复权价灌进后复权序列(2026-06-10 全市场假崩盘事故根因)。
            # 宁可当日缺数等下次更新,不可写入错口径。
            arr = k.get("hfqday") or []
            if not arr:
                break
            new = [r for r in arr if r[0] not in seen]
            if not new:
                break
            for r in new:
                seen.add(r[0])
            rows = new + rows                     # 早的拼前面
            earliest = arr[0][0]
            if earliest <= self.start:
                break
            end = earliest                        # END前移继续往前拿
        if not rows:
            return None
        df = pd.DataFrame([r[:6] for r in rows],
                          columns=["date", "open", "close", "high", "low", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        for c in ["open", "close", "high", "low", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
        df = df[df["date"] >= pd.Timestamp(self.start)]
        df["amount"] = df["volume"] * df["close"]      # 成交额近似(腾讯日线无amount)
        return df
