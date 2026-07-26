"use client";

import { useEffect, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import ResearchNav from "@/components/research/ResearchNav";
import { api } from "@/lib/api";
import KnowledgeGraphView from "../KnowledgeGraphView";

interface ChainNode { name: string; evidence: string; }
interface Chain {
  industry: string;
  mechanism_summary: string;
  target_hypothesis_name: string;
  nodes: ChainNode[];
}

export default function ResearchEvidencePage() {
  const [mode, setMode] = useState<"chains" | "graph">("chains");
  const [chains, setChains] = useState<Chain[]>([]);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    api.logicalChains().then(setChains).catch((e) => setMessage(String(e)));
  }, []);

  async function createDraft(chain: Chain) {
    setBusy(chain.target_hypothesis_name);
    setMessage("");
    try {
      const draft = await api.createResearchDraft({
        title: chain.target_hypothesis_name || `${chain.industry}研究草案`,
        source: "report",
        mechanism: chain.mechanism_summary,
        citation: chain.nodes.map((node) => node.evidence).filter(Boolean).join("；"),
      });
      setMessage(`已创建研究草案 ${draft.draft_id}；补全可执行因子定义后方可进入 L0。`);
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader title="研究素材库" desc="研报逻辑链与产业知识图谱仅作为假设来源；叙事先转草案，不直接进入回测" />
      <ResearchNav />
      <div className="inline-flex rounded-lg border border-line p-0.5 bg-jilan/30">
        <button onClick={() => setMode("chains")} className={`px-3 py-1.5 text-[12px] rounded-md ${mode === "chains" ? "bg-white text-brand shadow-sm" : "text-subink"}`}>逻辑链</button>
        <button onClick={() => setMode("graph")} className={`px-3 py-1.5 text-[12px] rounded-md ${mode === "graph" ? "bg-white text-brand shadow-sm" : "text-subink"}`}>知识图谱</button>
      </div>
      {message && <div className="card text-sm text-subink">{message}</div>}
      {mode === "graph" ? <KnowledgeGraphView /> : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {chains.map((chain) => (
            <div key={`${chain.industry}-${chain.target_hypothesis_name}`} className="card space-y-3">
              <div className="flex justify-between gap-3">
                <div>
                  <div className="text-[10px] text-brand font-semibold uppercase">{chain.industry}</div>
                  <h3 className="font-bold text-ink mt-1">{chain.target_hypothesis_name}</h3>
                </div>
                <button onClick={() => createDraft(chain)} disabled={busy === chain.target_hypothesis_name} className="px-3 py-1.5 rounded-lg bg-brand text-white text-[11px] h-fit disabled:opacity-50">
                  {busy === chain.target_hypothesis_name ? "创建中…" : "创建研究草案"}
                </button>
              </div>
              <p className="text-[12px] text-ink/80">{chain.mechanism_summary}</p>
              <div className="flex flex-wrap gap-1.5">
                {chain.nodes.map((node, index) => <span key={index} title={node.evidence} className="px-2 py-1 rounded border border-line text-[10px] text-subink">{node.name}</span>)}
              </div>
            </div>
          ))}
          {chains.length === 0 && <div className="card text-sm text-subink">暂无研报逻辑链数据。</div>}
        </div>
      )}
    </div>
  );
}
