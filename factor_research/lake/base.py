"""
下载地基：限流器 + 下载基类（限流/退避/会话复用/断点续传/并发）

设计依据（实测+参考simonlin1212/a-stock-data）：
- 网络IO密集 → 多线程(ThreadPoolExecutor)，非多进程
- 东财封禁阈值：秒>5/并发≥10/分钟≥200 → 默认并发≤6、间隔≥1s+抖动
- 各源独立限流器（阈值不同）
"""
from app_config.log import get_logger

logger = get_logger(__name__)

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 给所有 requests 请求加默认超时（防东财等接口hang卡死整个下载）
try:
    import requests as _rq
    _orig_request = _rq.sessions.Session.request
    def _request_with_timeout(self, *args, **kwargs):
        kwargs.setdefault("timeout", 20)
        return _orig_request(self, *args, **kwargs)
    _rq.sessions.Session.request = _request_with_timeout
except ImportError:
    pass


class RateLimiter:
    """线程安全限流器：保证相邻请求最小间隔 + 随机抖动（防封）"""
    def __init__(self, min_interval: float = 1.0, jitter: tuple = (0.2, 0.5)):
        self.min_interval = min_interval
        self.jitter = jitter
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.monotonic()
            gap = now - self._last
            need = self.min_interval - gap + random.uniform(*self.jitter)
            if need > 0:
                time.sleep(need)
            self._last = time.monotonic()


class Fetcher:
    """
    下载基类。子类只需实现 fetch_one(key) -> DataFrame|None。
    基类负责：限流、重试退避、断点续传、并发调度、失败收集。
    """
    def __init__(self, name: str, out_dir: str, limiter: RateLimiter = None,
                 max_workers: int = 6, retries: int = 3, backoff: float = 0.5,
                 timeout: float = 25):
        self.name = name
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.limiter = limiter or RateLimiter()
        self.max_workers = max_workers
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout      # 单请求超时(防东财等接口hang卡死)
        import socket
        socket.setdefaulttimeout(timeout)   # 全局socket超时，让网络请求本身超时

    # ── 子类实现 ──
    def fetch_one(self, key: str):
        """拉取单个标的数据，返回 DataFrame（空/None 表示无数据）"""
        raise NotImplementedError

    def out_path(self, key: str) -> Path:
        return self.out_dir / f"{key}.parquet"

    # ── 基类逻辑 ──
    def _fetch_retry(self, key: str):
        """带限流+daemon线程超时+指数退避重试。
        超时后daemon线程后台自灭、不阻塞主流程(可靠解决akshare请求hang)。"""
        for attempt in range(self.retries):
            try:
                self.limiter.wait()
                box = {}
                def _target(box=box):
                    try:
                        box["df"] = self.fetch_one(key)
                    except Exception as e:
                        box["err"] = e
                th = threading.Thread(target=_target, daemon=True)
                th.start()
                th.join(self.timeout)
                if th.is_alive():
                    raise TimeoutError(f"timeout>{self.timeout}s")
                if "err" in box:
                    raise box["err"]
                df = box.get("df")
                if df is None or len(df) == 0:
                    return ("empty", key, None)
                df.to_parquet(self.out_path(key), index=False)
                return ("ok", key, len(df))
            except Exception as e:
                if attempt == self.retries - 1:
                    return ("error", key, str(e)[:60])
                time.sleep(self.backoff * (2 ** attempt))
        return ("error", key, "max_retries")

    def run(self, keys: list, skip_existing: bool = True, progress_every: int = 200):
        """
        并发下载一批标的。
        skip_existing=True 时跳过已存在文件（断点续传）。
        返回统计字典 + 失败清单。
        """
        todo = []
        for k in keys:
            out = self.out_path(k)
            if skip_existing and out.exists():
                # 检查文件是否包含最新数据(文件日期 < 2天前 → 可能过期)
                try:
                    import pandas as pd
                    df = pd.read_parquet(out)
                    if 'date' in df.columns and len(df) > 0:
                        last = pd.Timestamp(df['date'].max())
                        if (pd.Timestamp.now() - last).days <= 2:
                            continue  # 数据新鲜, 跳过
                except Exception:
                    pass
            todo.append(k)
        cached = len(keys) - len(todo)
        stats = {"ok": 0, "empty": 0, "error": 0, "cached": cached}
        failures = []
        t0 = time.time()

        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {ex.submit(self._fetch_retry, k): k for k in todo}
            for i, fut in enumerate(as_completed(futs)):
                status, key, info = fut.result()
                stats[status] = stats.get(status, 0) + 1
                if status == "error":
                    failures.append((key, info))
                if (i + 1) % progress_every == 0:
                    el = time.time() - t0
                    eta = el / (i + 1) * (len(todo) - i - 1)
                    logger.info(f"  [{self.name}] {i+1}/{len(todo)} "
                          f"ok={stats['ok']} empty={stats['empty']} err={stats['error']} "
                          f"用时={el:.0f}s ETA={eta:.0f}s")

        stats["elapsed"] = round(time.time() - t0, 1)
        stats["failures"] = failures
        logger.info(f"[{self.name}] 完成 ok={stats['ok']} empty={stats['empty']} "
              f"err={stats['error']} cached={cached} 用时={stats['elapsed']}s")
        return stats

    def retry_failures(self, failures: list):
        """对失败清单单独重试一轮"""
        if not failures:
            return {"ok": 0, "error": 0}
        keys = [f[0] for f in failures]
        logger.warning(f"[{self.name}] 重试 {len(keys)} 个失败标的...")
        return self.run(keys, skip_existing=False)
