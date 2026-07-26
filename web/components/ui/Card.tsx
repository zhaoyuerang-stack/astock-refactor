import type { ReactNode } from "react";

type Tone = "default" | "ok" | "warn" | "danger";

const dotColor: Record<Tone, string> = {
  default: "bg-subink/60",
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
};

// 全站统一卡片:复用全局 .card 样式,标题行 + 右槽 + 内容区。
export default function Card({
  title,
  subtitle,
  right,
  tone = "default",
  className = "",
  children,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  tone?: Tone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`card ${className}`}>
      {(title || right) && (
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="min-w-0">
            {title && (
              <div className="text-sm font-semibold text-ink flex items-center gap-1.5">
                {tone !== "default" && <span className={`inline-block w-1.5 h-1.5 rounded-full ${dotColor[tone]}`} />}
                {title}
              </div>
            )}
            {subtitle && <div className="text-[11px] text-subink mt-0.5">{subtitle}</div>}
          </div>
          {right && <div className="shrink-0 text-[10px] text-subink font-quant">{right}</div>}
        </div>
      )}
      {children}
    </div>
  );
}
