"use client";

import { useEffect } from "react";

/**
 * 数据自动刷新:挂载即拉取 + 每 intervalMs 轮询 + 窗口重新聚焦立即刷新。
 * load 必须是 useCallback 包裹的稳定引用,否则每次渲染都会重建定时器。
 */
export function useAutoRefresh(load: () => void, intervalMs = 30000) {
  useEffect(() => {
    load();
    const id = setInterval(load, intervalMs);
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load, intervalMs]);
}
