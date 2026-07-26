"use client";

import { useState } from "react";

// 1. StatusCard
export type StatusCardProps = {
  title: string;
  status: "success" | "warning" | "danger" | "neutral";
  value: string | number;
  subtitle?: string;
  delta?: string;
  onClick?: () => void;
};

export function StatusCard({ title, status, value, subtitle, delta, onClick }: StatusCardProps) {
  const statusColors = {
    success: "border-[#35D06E] text-[#35D06E]",
    warning: "border-[#F6B73C] text-[#F6B73C]",
    danger: "border-[#FF5C5C] text-[#FF5C5C]",
    neutral: "border-[#1F3550] text-[#8FA3BF]",
  };

  const statusBg = {
    success: "bg-[#35D06E]/5",
    warning: "bg-[#F6B73C]/5",
    danger: "bg-[#FF5C5C]/5",
    neutral: "bg-[#0E2238]/5",
  };

  return (
    <div
      onClick={onClick}
      className={`p-4 border rounded-lg ${statusColors[status]} ${statusBg[status]} ${
        onClick ? "cursor-pointer hover:bg-opacity-10 transition-all" : ""
      }`}
    >
      <div className="text-[11px] uppercase tracking-wider text-[#8FA3BF]">{title}</div>
      <div className="text-2xl font-bold mt-1.5 font-mono">{value}</div>
      {(subtitle || delta) && (
        <div className="flex items-center justify-between mt-2 text-[11px]">
          <span className="text-[#5F728A]">{subtitle}</span>
          {delta && <span className="font-semibold font-mono">{delta}</span>}
        </div>
      )}
    </div>
  );
}

// 2. MetricCard compliant with requirements
export type MetricCardProps = {
  label: string;
  value: string | number;
  unit?: string;
  delta?: number;
  deltaLabel?: string;
  intent?: "positive" | "negative" | "neutral";
  precision?: number;
};

export function QuantMetricCard({
  label,
  value,
  unit,
  delta,
  deltaLabel,
  intent = "neutral",
  precision = 2,
}: MetricCardProps) {
  const intents = {
    positive: "text-[#35D06E]",
    negative: "text-[#FF5C5C]",
    neutral: "text-[#E6EDF7]",
  };

  const formattedValue = typeof value === "number" ? value.toFixed(precision) : value;

  return (
    <div className="p-4 bg-[#0E2238] border border-[#1F3550] rounded-lg">
      <div className="text-[12px] text-[#8FA3BF]">{label}</div>
      <div className="flex items-baseline gap-1 mt-1.5">
        <span className={`text-2xl font-bold font-mono ${intents[intent]}`}>{formattedValue}</span>
        {unit && <span className="text-xs text-[#5F728A] font-mono">{unit}</span>}
      </div>
      {delta !== undefined && (
        <div className={`text-[11px] mt-2 font-mono flex items-center gap-1 ${delta >= 0 ? "text-[#35D06E]" : "text-[#FF5C5C]"}`}>
          <span>{delta >= 0 ? "▲" : "▼"}</span>
          <span>
            {Math.abs(delta).toFixed(precision)}% {deltaLabel}
          </span>
        </div>
      )}
    </div>
  );
}

// 3. GateCard
export type GateCardProps = {
  name: string;
  status: "passed" | "warning" | "failed" | "pending";
  summary: string;
  lastCheckedAt?: string;
  evidenceUrl?: string;
};

export function GateCard({ name, status, summary, lastCheckedAt, evidenceUrl }: GateCardProps) {
  const badgeColors = {
    passed: "text-[#35D06E] bg-[#35D06E]/10 border-[#35D06E]/20",
    warning: "text-[#F6B73C] bg-[#F6B73C]/10 border-[#F6B73C]/20",
    failed: "text-[#FF5C5C] bg-[#FF5C5C]/10 border-[#FF5C5C]/20",
    pending: "text-[#9AA8BD] bg-[#10263D] border-[#1F3550]",
  };

  const labels = {
    passed: "PASS",
    warning: "WARN",
    failed: "FAIL",
    pending: "PENDING",
  };

  return (
    <div className="p-4 bg-[#0E2238] border border-[#1F3550] rounded-lg flex flex-col justify-between h-full hover:border-[#3D7BFF] transition-all">
      <div>
        <div className="flex justify-between items-start gap-2">
          <span className="font-bold text-[13px] text-[#E6EDF7] font-mono">{name}</span>
          <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${badgeColors[status]}`}>
            {labels[status]}
          </span>
        </div>
        <p className="text-[12px] text-[#8FA3BF] mt-2 leading-relaxed">{summary}</p>
      </div>
      <div className="mt-4 pt-2 border-t border-[#1F3550]/50 flex justify-between items-center text-[10px] text-[#5F728A] font-mono">
        <span>{lastCheckedAt ? `Checked: ${lastCheckedAt}` : "Not checked"}</span>
        {evidenceUrl && (
          <a href={evidenceUrl} className="text-[#3D7BFF] hover:underline flex items-center gap-0.5">
            🔍 證據
          </a>
        )}
      </div>
    </div>
  );
}

// 4. RiskBadge
export type RiskBadgeProps = {
  level: "low" | "medium" | "high" | "blocked";
  label?: string;
};

export function RiskBadge({ level, label }: RiskBadgeProps) {
  const colors = {
    low: "text-[#35D06E] bg-[#35D06E]/10 border-[#35D06E]/20",
    medium: "text-[#F6B73C] bg-[#F6B73C]/10 border-[#F6B73C]/20",
    high: "text-[#FF5C5C] bg-[#FF5C5C]/10 border-[#FF5C5C]/20",
    blocked: "text-white bg-[#FF5C5C] border-none font-bold animate-pulse",
  };

  const text = label || level.toUpperCase();

  return (
    <span className={`inline-block px-2 py-0.5 rounded text-[10px] border font-mono ${colors[level]}`}>
      {text}
    </span>
  );
}

// 5. HashCopy
export type HashCopyProps = {
  value: string;
  label?: string;
  short?: boolean;
};

export function HashCopy({ value, label, short = true }: HashCopyProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const displayVal = short && value.length > 12 ? `${value.slice(0, 6)}...${value.slice(-6)}` : value;

  return (
    <div className="inline-flex items-center gap-1.5 bg-[#081827] border border-[#1F3550] rounded px-2 py-1 text-[11px] font-mono text-[#8FA3BF] select-all">
      {label && <span className="text-[#5F728A]">{label}:</span>}
      <span className="text-[#E6EDF7] font-semibold">{displayVal}</span>
      <button
        onClick={handleCopy}
        className="text-[#3D7BFF] hover:text-[#E6EDF7] focus:outline-none cursor-pointer transition-colors"
        title="複製 Hash"
      >
        {copied ? "✓" : "📋"}
      </button>
    </div>
  );
}

// 6. DataFreshnessBadge
export function DataFreshnessBadge({ daysAgo }: { daysAgo: number }) {
  if (daysAgo === 0) {
    return <span className="px-2 py-0.5 rounded text-[10px] bg-[#35D06E]/10 text-[#35D06E] border border-[#35D06E]/20 font-bold font-mono">FRESH</span>;
  }
  if (daysAgo <= 1) {
    return <span className="px-2 py-0.5 rounded text-[10px] bg-[#F6B73C]/10 text-[#F6B73C] border border-[#F6B73C]/20 font-bold font-mono">T-1 LAG</span>;
  }
  return <span className="px-2 py-0.5 rounded text-[10px] bg-[#FF5C5C]/10 text-[#FF5C5C] border border-[#FF5C5C]/20 font-bold font-mono">STALE ({daysAgo}D)</span>;
}

// 7. StrategyStatusBadge
export function StrategyStatusBadge({ status }: { status: "ACTIVE" | "REFERENCE" | "CANDIDATE" | "FALSIFIED" | "RETIRED" | string }) {
  const colors: Record<string, string> = {
    ACTIVE: "text-[#35D06E] bg-[#35D06E]/10 border-[#35D06E]/20",
    REFERENCE: "text-[#3D7BFF] bg-[#3D7BFF]/10 border-[#3D7BFF]/20",
    CANDIDATE: "text-[#9AA8BD] bg-[#10263D] border-[#1F3550]",
    FALSIFIED: "text-[#FF5C5C] bg-[#FF5C5C]/10 border-[#FF5C5C]/20",
    RETIRED: "text-[#9AA8BD] bg-[#0E2238] border-[#1F3550] opacity-60",
  };
  return (
    <span className={`px-2 py-0.5 rounded text-[10px] font-bold border font-mono ${colors[status] || colors.CANDIDATE}`}>
      {status}
    </span>
  );
}

// 8. PipelineStepper
export interface PipelineStep {
  name: string;
  count: number | string;
  desc?: string;
  status: "completed" | "active" | "pending" | "warning";
}

export function PipelineStepper({ steps }: { steps: PipelineStep[] }) {
  return (
    <div className="flex flex-col md:flex-row items-stretch md:items-center justify-between gap-4 py-2 font-mono">
      {steps.map((step, idx) => {
        const borderColors = {
          completed: "border-[#35D06E]",
          active: "border-[#3D7BFF] bg-[#3D7BFF]/5",
          warning: "border-[#F6B73C] bg-[#F6B73C]/5",
          pending: "border-[#1F3550]",
        };
        const textColors = {
          completed: "text-[#35D06E]",
          active: "text-[#3D7BFF]",
          warning: "text-[#F6B73C]",
          pending: "text-[#5F728A]",
        };

        return (
          <div key={idx} className="flex-1 flex items-center gap-3">
            <div className={`flex-1 p-3 border rounded-lg ${borderColors[step.status]} relative`}>
              <div className="flex justify-between items-center">
                <span className="text-[12px] font-bold text-[#E6EDF7]">{step.name}</span>
                <span className={`text-[13px] font-bold ${textColors[step.status]}`}>{step.count}</span>
              </div>
              {step.desc && <div className="text-[10px] text-[#8FA3BF] mt-1">{step.desc}</div>}
            </div>
            {idx < steps.length - 1 && (
              <span className="hidden md:inline text-[#1F3550] text-lg font-bold">→</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// 9. LoadingSkeleton
export function LoadingSkeleton() {
  return (
    <div className="space-y-4 animate-pulse p-4">
      <div className="h-6 bg-[#1f3550]/40 rounded w-1/4"></div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="h-28 bg-[#1f3550]/30 rounded"></div>
        <div className="h-28 bg-[#1f3550]/30 rounded"></div>
        <div className="h-28 bg-[#1f3550]/30 rounded"></div>
      </div>
      <div className="h-40 bg-[#1f3550]/20 rounded"></div>
    </div>
  );
}
