"""Canonical logging 入口(R-ARCH-003 统一入口精神,评审 2026-07-18 观测层缺口)。

背景:canonical 库层(lake/workflow/factors/core)此前 ~150 处 print() 散喷
stdout,无时间戳/级别/模块名,对"可审计"P1 目标是观测缺口。本模块是唯一
的 logger 工厂;库层代码一律 ``get_logger(__name__)``,禁止再新增裸 print。

设计取舍(研究单仓,不是纯库):
- handler 挂在**顶层命名空间**(lake/workflow/...)上,get_logger 首次调用时
  惰性装配,幂等——研究脚本数百个入口,无法要求每个入口先 setup;
- 输出到 **stdout**(不是 logging 默认的 stderr),保持既有 shell 重定向/
  管道捕获行为不变;
- 级别默认 INFO,环境变量 ``FACTOR_LOG_LEVEL`` 可覆盖(如 DEBUG/WARNING)。
"""
from __future__ import annotations

import logging
import os
import sys

_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str) -> logging.Logger:
    """按模块名取 logger;首次触达某顶层命名空间时幂等装配 stdout handler。"""
    top = logging.getLogger(name.split(".", 1)[0])
    if not top.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_FMT, _DATEFMT))
        top.addHandler(handler)
        top.setLevel(os.environ.get("FACTOR_LOG_LEVEL", "INFO").upper())
        top.propagate = False
    return logging.getLogger(name)
