"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  ["/experiments", "工作队列"],
  ["/experiments/evidence", "研究素材"],
  ["/experiments/runs", "运行记录"],
  ["/experiments/reviews", "人工复核"],
] as const;

export default function ResearchNav() {
  const pathname = usePathname();
  return (
    <div className="flex flex-wrap gap-1.5 border-b border-line/40 pb-3">
      {ITEMS.map(([href, label]) => {
        const active = href === "/experiments" ? pathname === href : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className={`px-3.5 py-1.5 rounded-lg text-[12px] transition-colors ${
              active ? "bg-brand text-white font-semibold" : "bg-white border border-line text-subink hover:text-ink"
            }`}
          >
            {label}
          </Link>
        );
      })}
    </div>
  );
}
