"""Tushare 数据源(dep-free HTTP,token 从环境变量 TUSHARE_TOKEN 读,绝不硬编码)。

tushare REST = 单个 POST 到 api.tushare.pro,{api_name, token, params, fields} →
{code, msg, data:{fields, items}}。比 SDK 少一个依赖,更可控。

限速:2000 积分 daily_basic ~200 次/分;命中限速(msg 含'每分钟'/'频率')→ sleep 60s 重试。
防封禁铁律:单线程顺序,绝不多线程。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

import pandas as pd

API_URL = "http://api.tushare.pro"
_MIN_INTERVAL = 0.18  # ~330 次/分上限留余量


_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "../../data_lake/agent/tushare_config.json")


def _token() -> str:
    tok = os.environ.get("TUSHARE_TOKEN")
    if not tok:
        # fallback: 读 data_lake/agent/tushare_config.json {"token": "xxx"}
        try:
            import json
            with open(os.path.normpath(_TOKEN_FILE)) as f:
                tok = json.load(f).get("token", "")
        except Exception:
            pass
    if not tok:
        raise RuntimeError(
            "缺 tushare token：设 TUSHARE_TOKEN 环境变量，"
            "或在 data_lake/agent/tushare_config.json 写入 {\"token\": \"xxx\"}"
        )
    return tok


_last_call = [0.0]


def call(api_name: str, params: dict, fields: str = "", *, max_retry: int = 6) -> pd.DataFrame:
    """调一个 tushare 接口 → DataFrame。限速/瞬时错误自动重试。

    重试 6 次:高并发下重响应接口(cyq_perf/limit_list_d)易瞬时超时,3 次不够。
    """
    body = json.dumps({"api_name": api_name, "token": _token(),
                       "params": params, "fields": fields}).encode()
    for attempt in range(max_retry):
        # 本地节流(单线程顺序)
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()
        try:
            req = urllib.request.Request(API_URL, data=body,
                                         headers={"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=60).read())
        except Exception:  # 网络瞬时错误 → 退避重试(指数,封顶 30s)
            if attempt == max_retry - 1:
                raise
            time.sleep(min(30, 3 * 2 ** attempt))
            continue
        code, msg = r.get("code"), (r.get("msg") or "")
        if code == 0:
            d = r.get("data") or {}
            return pd.DataFrame(d.get("items") or [], columns=d.get("fields") or [])
        # 硬配额(X次/天 或 X次/小时)→ 重试无用,直接抛(否则白等 6×60s)
        if "次/天" in msg or "次/小时" in msg:
            raise RuntimeError(f"tushare {api_name} 配额墙[{code}](需更高积分): {msg[:80]}")
        # 每分钟软限速(含 40203「频率超限(200次/分钟)」)→ 等 60s 重试,不当致命
        if "每分钟" in msg or "频率超限" in msg or ("分钟" in msg and "次/天" not in msg and "次/小时" not in msg):
            time.sleep(60)
            continue
        raise RuntimeError(f"tushare {api_name} 错误[{code}]: {msg[:80]}")
    raise RuntimeError(f"tushare {api_name} 重试 {max_retry} 次仍失败")


# ── ts_code(600519.SH)↔ 本仓库 code(600519)──
def to_code(ts_code: pd.Series) -> pd.Series:
    return ts_code.str.split(".").str[0]
