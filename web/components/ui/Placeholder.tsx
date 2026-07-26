"use client";

import { useEffect } from "react";
import PageHeader from "./PageHeader";
import { useAgent } from "@/lib/agentStore";

export default function Placeholder({ title, phase }: { title: string; phase: string }) {
  const setContext = useAgent((s) => s.setContext);
  useEffect(() => {
    setContext({
      page: title,
      title: `${title}(建设中)`,
      summary: `${title} 将在 ${phase} 接入。本页骨架已就位,等待对应 services 接线。`,
    });
  }, [title, phase, setContext]);
  return (
    <div>
      <PageHeader title={title} desc="建设中 — 路线见 Implement.md" />
      <div className="card text-sm text-subink">
        该模块计划于 <span className="text-ink font-medium">{phase}</span> 接入真实数据与控制回路。
      </div>
    </div>
  );
}
