import { statusLabel } from "@/lib/researchWorkspace.mjs";

const TONES: Record<string, string> = {
  review: "bg-brand/10 text-brand border-brand/30",
  blocked: "bg-danger/10 text-danger border-danger/30",
  ready: "bg-songshi/10 text-songshi border-songshi/30",
  running: "bg-warn/10 text-warn border-warn/30",
  completed: "bg-ink/5 text-ink border-line",
  archived: "bg-subink/5 text-subink border-line",
};

export default function WorkItemBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex px-2 py-0.5 rounded border text-[10px] ${TONES[status] ?? TONES.archived}`}>
      {statusLabel(status)}
    </span>
  );
}
