"use client";

import { useEffect, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import ResearchNav from "@/components/research/ResearchNav";
import { api } from "@/lib/api";
import type { ActionJobView, ResearchRunIndexView } from "@/lib/types";

export default function ResearchRunsPage() {
  const [runs, setRuns] = useState<ResearchRunIndexView | null>(null);
  const [jobs, setJobs] = useState<ActionJobView[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.researchRuns(), api.researchJobs()])
      .then(([r, j]) => { setRuns(r); setJobs(j); })
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="space-y-5">
      <PageHeader title="研究运行记录" desc="当前异步任务 + append-only 研究结论，保留失败原因与数据 vintage" />
      <ResearchNav />
      {error && <div className="card text-sm text-danger">{error}</div>}
      <div className="card">
        <h2 className="font-semibold text-sm mb-3">当前任务</h2>
        <div className="space-y-2">
          {jobs.slice(0, 20).map((job) => (
            <div key={job.job_id} className="flex justify-between gap-3 border-t border-line/50 pt-2 text-[12px]">
              <span className="font-mono text-ink">{job.kind}</span>
              <span className={job.status === "failed" ? "text-danger" : job.status === "succeeded" ? "text-songshi" : "text-warn"}>{job.status}</span>
              <span className="text-subink">{job.finished_at || job.started_at || job.created_at}</span>
            </div>
          ))}
          {jobs.length === 0 && <div className="text-sm text-subink">当前进程暂无 Job。</div>}
        </div>
      </div>
      <div className="card overflow-x-auto">
        <h2 className="font-semibold text-sm mb-3">研究结论台账</h2>
        <table className="w-full text-[12px] min-w-[780px]">
          <thead className="text-subink text-left"><tr><th className="py-2">时间</th><th>研究对象</th><th>来源</th><th>结论</th><th>下一步</th><th>数据 vintage</th></tr></thead>
          <tbody>
            {(runs?.latest_runs ?? []).map((run, index) => (
              <tr key={`${run.run_id}-${index}`} className="border-t border-line/50">
                <td className="py-2 text-subink">{run.run_at}</td>
                <td className="font-semibold text-ink">{run.hypothesis}</td>
                <td className="text-subink">{run.source}</td>
                <td className={run.verdict === "PASS" ? "text-songshi" : run.verdict === "REFUTED" ? "text-danger" : "text-warn"}>{run.verdict}</td>
                <td className="text-brand">{run.next_action}</td>
                <td className="font-mono text-[10px] text-subink">{JSON.stringify(run.data_vintage)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
