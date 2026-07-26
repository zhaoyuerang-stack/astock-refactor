"use client";

import { useCallback } from "react";
import { useLayoutStore } from "@/lib/layoutStore";

// 三栏之间的拖拽条:按下拖动调宽,双击复位默认宽度。对应栏折叠时不渲染。
export default function ResizeHandle({ target }: { target: "sidebar" | "agent" }) {
  const isSidebar = target === "sidebar";

  const collapsed = useLayoutStore((s) => (isSidebar ? s.sidebarCollapsed : s.agentCollapsed));

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const st = useLayoutStore.getState();
      const startWidth = isSidebar ? st.sidebarWidth : st.agentWidth;
      const setWidth = isSidebar ? st.setSidebarWidth : st.setAgentWidth;

      const onMove = (ev: MouseEvent) => {
        const delta = ev.clientX - startX;
        // 左栏:右移变宽;右栏:右移变窄(方向相反)
        setWidth(startWidth + (isSidebar ? delta : -delta));
      };
      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };

      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
      // 拖拽期间锁定光标与禁止选中文本
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [isSidebar],
  );

  const onDoubleClick = useCallback(() => {
    const st = useLayoutStore.getState();
    if (isSidebar) st.resetSidebar();
    else st.resetAgent();
  }, [isSidebar]);

  if (collapsed) return null;

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={isSidebar ? "拖动调整导航栏宽度" : "拖动调整 AI 助手栏宽度"}
      title="拖动调整宽度，双击复位"
      onMouseDown={onMouseDown}
      onDoubleClick={onDoubleClick}
      className="group w-[5px] shrink-0 h-screen sticky top-0 cursor-col-resize flex items-center justify-center z-20"
    >
      <span className="block w-px h-full bg-[#3C4654]/30 group-hover:bg-[#5AA4AE] group-hover:w-[2px] transition-all duration-150" />
    </div>
  );
}
