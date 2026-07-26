"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { NAV_GROUPS } from "@/lib/nav";
import { useWorkspaceStore } from "@/lib/workspaceStore";
import { useLayoutStore } from "@/lib/layoutStore";
import { useAppStore } from "@/lib/appStore";

function SidebarIcon({ name }: { name: string }) {
  switch (name) {
    case "dashboard":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
        </svg>
      );
    case "signals":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
        </svg>
      );
    case "candidates":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
        </svg>
      );
    case "plans":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
      );
    case "data":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4" />
        </svg>
      );
    case "factors":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 9.172V5L8 4z" />
        </svg>
      );
    case "backtest":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 002 2h2a2 2 0 002-2z" />
        </svg>
      );
    case "experiments":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 9.172V5L8 4z" />
        </svg>
      );
    case "portfolio":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M11 3.055A9.001 9.001 0 1020.945 13H11V3.055z" />
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M20.488 9H15V3.512A9.025 9.025 0 0120.488 9z" />
        </svg>
      );
    case "risk":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
        </svg>
      );
    case "settings":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
      );
    default:
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5l7 7-7 7" />
        </svg>
      );
  }
}

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { mode, setMode } = useWorkspaceStore();
  const { latestDataDate, selectedStrategyId, selectedStrategyVersion } = useAppStore();

  // URL-driven auto mode synchronization (e.g. bookmarks or back/forward buttons)
  useEffect(() => {
    const activeItem = NAV_GROUPS.flatMap((g) => g.items).find(
      (item) => pathname === item.href || pathname.startsWith(item.href + "/")
    );
    if (activeItem) {
      if (activeItem.modes.includes(mode)) {
        return; // Valid in current mode, no change
      }
      // If it is exclusively valid in the other mode, switch workspace mode
      if (activeItem.modes.includes("ops")) {
        setMode("ops");
      } else if (activeItem.modes.includes("rd")) {
        setMode("rd");
      }
    }
  }, [pathname, mode, setMode]);

  const handleModeChange = (newMode: "ops" | "rd") => {
    if (newMode === mode) return;
    setMode(newMode);

    // Redirect to default page if current page isn't allowed in new mode
    const activeItem = NAV_GROUPS.flatMap((g) => g.items).find(
      (item) => pathname === item.href || pathname.startsWith(item.href + "/")
    );

    if (!activeItem || !activeItem.modes.includes(newMode)) {
      if (newMode === "ops") {
        router.push("/dashboard");
      } else {
        router.push("/factor-research");
      }
    }
  };

  // Filter groups and items by workspace mode
  const filteredGroups = NAV_GROUPS.map((group) => {
    const items = group.items.filter((item) => item.modes.includes(mode));
    return { ...group, items };
  }).filter((group) => group.items.length > 0);

  const width = useLayoutStore((s) => s.sidebarWidth);
  const collapsed = useLayoutStore((s) => s.sidebarCollapsed);
  const toggle = useLayoutStore((s) => s.toggleSidebar);

  // 折叠态:仅保留浮动复原按钮
  if (collapsed) {
    return (
      <button
        onClick={toggle}
        aria-label="展开导航栏"
        title="展开导航栏"
        className="fixed left-2 top-3 z-50 w-8 h-8 flex items-center justify-center rounded-md bg-jilan border border-line/40 text-subink hover:text-ink hover:border-brand/40 transition-colors"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5l7 7-7 7" />
        </svg>
      </button>
    );
  }

  return (
    <aside
      style={{ width }}
      className="shrink-0 bg-[#161617] border-r border-line/45 text-ink flex flex-col h-screen sticky top-0 font-sans shadow-sm"
    >
      {/* Title Header */}
      <div className="px-5 py-5 border-b border-line/30 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-bold tracking-wide text-ink font-quant">Quant OS</div>
          <div className="text-[10px] text-subink mt-0.5 tracking-wide">清晨 · 窗前微光 · 算筹与朱砂</div>
        </div>
        <button
          onClick={toggle}
          aria-label="折叠导航栏"
          title="折叠导航栏"
          className="shrink-0 mt-0.5 w-6 h-6 flex items-center justify-center rounded-md text-subink/70 hover:text-ink hover:bg-line/45 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
      </div>

      {/* Mode Switcher */}
      <div className="px-3 pt-4 pb-1">
        <div className="bg-bg/85 p-1 rounded-lg border border-line/45 flex gap-1 text-[11px] font-semibold">
          <button
            onClick={() => handleModeChange("ops")}
            title="行动桌面:用已登记策略产出信号并执行(信号 → 选股 → 签发 → 风控)"
            className={`flex-1 py-1.5 rounded-md flex items-center justify-center gap-1 transition-all duration-200 ${
              mode === "ops"
                ? "bg-songshi/10 text-songshi border border-songshi/30 font-semibold shadow-sm"
                : "text-subink/70 hover:text-ink hover:bg-line/20"
            }`}
          >
            <span>⚡</span> 行动桌面
          </button>
          <button
            onClick={() => handleModeChange("rd")}
            title="研发实验室:发现并验证新的 alpha(数据 → 候选 → 回测 → 登记)"
            className={`flex-1 py-1.5 rounded-md flex items-center justify-center gap-1 transition-all duration-200 ${
              mode === "rd"
                ? "bg-brand/10 text-brand border border-brand/35 font-semibold shadow-sm"
                : "text-subink/70 hover:text-ink hover:bg-line/20"
            }`}
          >
            <span>🧪</span> 研发实验室
          </button>
        </div>
      </div>
      
      {/* Navigation */}
      <nav className="flex-1 py-4 overflow-y-auto space-y-5 px-3">
        {filteredGroups.map((group) => (
          <div key={group.title} className="space-y-1">
            <div className="px-3 text-[10px] uppercase tracking-widest text-subink font-bold opacity-80">
              {group.title}
            </div>
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const active = pathname === item.href || pathname.startsWith(item.href + "/");
                return (
                  <Link
                    key={item.href}
                    href={item.ready ? item.href : "#"}
                    title={item.desc}
                    aria-current={active ? "page" : undefined}
                    className={`flex items-start gap-2.5 px-3 py-1.5 rounded transition-all duration-200 text-[12px] group ${
                      active
                        ? "bg-brand/10 text-brand font-bold border-l-2 border-brand rounded-l-none"
                        : item.ready
                        ? "text-ink/75 hover:bg-line/20 hover:text-ink"
                        : "text-subink/45 cursor-not-allowed"
                    }`}
                  >
                    <span className={`mt-[1px] shrink-0 ${active ? "text-brand" : "text-subink group-hover:text-ink/80"}`}>
                      <SidebarIcon name={item.icon} />
                    </span>
                    <span className="flex-1 min-w-0">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate">{item.label}</span>
                        {!item.ready && (
                          <span className="text-[8px] text-subink/50 border border-line/20 rounded px-1 scale-90 tracking-tighter">
                            建设
                          </span>
                        )}
                      </span>
                      {/* 当前页自解释:仅活跃项展开一行职责说明,避免给其它项增加噪音 */}
                      {active && (
                        <span className="block text-[10px] leading-snug font-normal text-brand/70 mt-0.5">
                          {item.desc}
                        </span>
                      )}
                    </span>
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      
      {/* Footer Status */}
      <div className="px-5 py-3.5 border-t border-line/30 text-[11px] text-subink/80 bg-jilan/25 font-mono space-y-1 bg-opacity-5 border-opacity-50">
        <div>當前策略：{selectedStrategyId ? `${selectedStrategyId} ${selectedStrategyVersion}` : "—"}</div>
        <div>系統版本：—</div>
        <div>數據日期：{latestDataDate}</div>
      </div>
    </aside>
  );
}
