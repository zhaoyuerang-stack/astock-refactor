import type { ReactNode } from "react";

type BannerStatus = "ready" | "blocked" | "attention" | "neutral";

// 态势横幅:页面顶部「一眼看清当前总体状态」的唯一权威呈现,消除多处重复。
// ops 用 ready/blocked 表达交易门禁;rd 用 ready/attention/blocked 表达实验室健康度。
const TONE: Record<BannerStatus, { wrap: string; dot: string; title: string }> = {
  ready:     { wrap: "border-ok/30 bg-ok/5",         dot: "bg-ok",     title: "text-ok" },
  blocked:   { wrap: "border-danger/30 bg-danger/5", dot: "bg-danger", title: "text-danger" },
  attention: { wrap: "border-warn/30 bg-warn/5",     dot: "bg-warn",   title: "text-warn" },
  neutral:   { wrap: "border-line/50 bg-bg",         dot: "bg-subink/50", title: "text-ink" },
};

export default function StatusBanner({
  status,
  title,
  detail,
}: {
  status: BannerStatus;
  title: ReactNode;
  detail?: ReactNode;
}) {
  const tone = TONE[status];
  return (
    <div className={`rounded-lg border px-5 py-4 flex items-start gap-3 ${tone.wrap}`}>
      <span className={`mt-1 inline-block w-2.5 h-2.5 rounded-full shrink-0 ${tone.dot}`} />
      <div className="min-w-0">
        <div className={`text-base font-semibold ${tone.title}`}>{title}</div>
        {detail && <div className="text-[12px] text-subink mt-1 leading-relaxed">{detail}</div>}
      </div>
    </div>
  );
}
