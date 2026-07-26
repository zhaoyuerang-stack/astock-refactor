"use client";

import type { PaperTradesView } from "@/lib/types";

const fmt = (x: number) => x.toLocaleString("zh-CN", { maximumFractionDigits: 0 });

export default function TradesTable({ data }: { data: PaperTradesView }) {
  if (data.trades.length === 0) {
    return <div className="card text-sm text-subink">暂无成交记录(模拟盘尚未发生买卖)。</div>;
  }
  return (
    <div className="card">
      <div className="text-sm font-medium mb-2">交易流水(共 {data.total} 笔,最新在前)</div>
      <div className="max-h-[480px] overflow-y-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="text-subink text-left border-b border-cardline sticky top-0 bg-[#24354D]">
              <th className="py-1 font-medium">日期</th>
              <th className="py-1 font-medium">方向</th>
              <th className="py-1 font-medium">代码</th>
              <th className="py-1 font-medium">名称</th>
              <th className="py-1 font-medium text-right">股数</th>
              <th className="py-1 font-medium text-right">价格</th>
              <th className="py-1 font-medium text-right">金额</th>
              <th className="py-1 font-medium text-right">费用</th>
              <th className="py-1 font-medium text-right">余币</th>
            </tr>
          </thead>
          <tbody>
            {data.trades.map((t, i) => (
              <tr key={`${t.date}-${t.code}-${i}`} className="border-b border-cardline/60">
                <td className="py-1 text-subink">{t.date}</td>
                <td className="py-1">
                  <span className={`inline-block px-1.5 py-0.5 rounded text-[11px] font-medium ${
                    t.side === "BUY" ? "bg-danger/10 text-danger" : "bg-ok/10 text-ok"
                  }`}>
                    {t.side === "BUY" ? "买入" : "卖出"}
                  </span>
                </td>
                <td className="py-1 text-ink">{t.code}</td>
                <td className="py-1 text-ink">{t.name}</td>
                <td className="py-1 text-right text-subink">{t.shares}</td>
                <td className="py-1 text-right text-subink">{t.price.toFixed(3)}</td>
                <td className="py-1 text-right text-ink">{fmt(t.notional)}</td>
                <td className="py-1 text-right text-subink">{t.cost.toFixed(1)}</td>
                <td className="py-1 text-right text-subink">{fmt(t.cash_after)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
