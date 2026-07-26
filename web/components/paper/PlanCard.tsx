"use client";

import type { TradePlanView } from "@/lib/types";

const fmt = (x: number) => x.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
const shortTime = (x: string) => x ? x.replace("T", " ").slice(0, 19) : "—";

function SideTag({ side }: { side: string }) {
  const buy = side === "BUY" || side === "买入";
  const hold = side === "HOLD";
  return (
    <span
      className={`inline-block px-1.5 py-0.5 rounded text-[11px] font-medium ${
        hold ? "bg-cardline text-subink" : buy ? "bg-danger/10 text-danger" : "bg-ok/10 text-ok"
      }`}
    >
      {hold ? "持有" : buy ? "买入" : "卖出"}
    </span>
  );
}

export default function PlanCard({ plan }: { plan: TradePlanView }) {
  const bear = plan.regime === "bear";
  const isStale = plan.stale;
  const bond = plan.bond;
  const isBondUnauthorized = bond?.authorized === false || bond?.side === "BLOCKED";
  const isBondNonExecutable = isStale || isBondUnauthorized;
  const staleSignalDate = plan.last_exec_signal_date || plan.signal_date || plan.account_date || "—";
  const bondSideText =
    bond?.side === "BUY" ? "买入" : bond?.side === "SELL" ? "卖出" : bond?.side === "HOLD" ? "持有" : bond?.side || "—";

  return (
    <div className="space-y-4">
      {isStale && (
        <div className="card text-sm text-warn border border-warn/30">
          <div className="font-semibold">冻结历史信号: 非现行可执行</div>
          <div className="text-[12px] text-subink mt-1">
            {plan.stale_reason || "纸面信号已过期"}。以下内容仅代表模拟盘历史状态,不得作为当前交易指引。
          </div>
        </div>
      )}

      <div className="card text-[12px] text-subink flex flex-wrap gap-x-4 gap-y-1">
        <span>模拟盘结算日: <b className="text-ink">{plan.account_date || plan.signal_date || "—"}</b></span>
        <span>本次执行信号: <b className="text-ink">{plan.last_exec_signal_date || "—"}</b></span>
        <span>视图刷新: <b className="text-ink font-mono">{shortTime(plan.generated_at)}</b></span>
      </div>

      {/* 债券轮动指令卡(P5)—— 最醒目位 */}
      {bond?.active && (
        <div className={`card border-2 ${isBondNonExecutable ? "border-warn/40" : bear ? "border-danger/40" : "border-ok/40"}`}>
          <div className="flex items-center gap-2 mb-2">
            <span className={`text-sm font-semibold ${isBondNonExecutable ? "text-warn" : bear ? "text-danger" : "text-ok"}`}>
              {isBondUnauthorized
                ? "未授权 defensive overlay: 非现行可执行"
                : isStale
                ? "冻结历史信号: 非现行可执行"
                : `Regime 轮动:${bear ? "BEAR" : "BULL"}(${(plan.regime_dist * 100).toFixed(2)}%)`}
            </span>
            {isBondNonExecutable ? (
              <span className="inline-block px-1.5 py-0.5 rounded text-[11px] font-medium bg-warn/10 text-warn">
                {isBondUnauthorized ? "未授权" : `历史${bondSideText}`}
              </span>
            ) : (
              <SideTag side={bond.side} />
            )}
          </div>
          <div className="text-sm text-ink">
            {isBondNonExecutable ? (
              <div className="space-y-1">
                <div>
                  {isBondUnauthorized
                    ? `${bond.code} ${bond.name} 缺少独立 defensive overlay 授权,非现行可执行。`
                    : `${bond.code} ${bond.name} 的${bondSideText}状态来自 ${staleSignalDate} 信号,非现行可执行。`}
                </div>
                <div className="text-[12px] text-subink">
                  {isBondUnauthorized
                    ? (bond.blocked_reason || "defensive overlay 未授权,拒绝债券轮动")
                    : `当前显示的 ${bond.shares_held} 份持仓和 ${bond.est_shares} 份估算只代表模拟盘遗留状态。`}
                </div>
              </div>
            ) : (
              <>
                {bond.side === "BUY" && (
                  <>次日将全部闲置资金买入 <b>{bond.code} {bond.name}</b>
                    {bond.est_shares > 0 && (
                      <> ≈ <b>{bond.est_shares}</b> 份 × {bond.ref_price.toFixed(3)} = <b>{fmt(bond.est_notional)}</b> 元</>
                    )}
                  </>
                )}
                {bond.side === "SELL" && (
                  <>次日开盘卖出全部 <b>{bond.code} {bond.name}</b> {bond.shares_held} 份
                    ≈ <b>{fmt(bond.est_notional)}</b> 元,资金买回股票</>
                )}
                {bond.side === "HOLD" && (
                  <>继续持有 <b>{bond.code} {bond.name}</b> {bond.shares_held} 份(闲置现金不足一手)</>
                )}
              </>
            )}
          </div>
          <div className="text-[11px] text-subink mt-1.5">{bond.note}</div>
        </div>
      )}

      {/* 今日成交 */}
      <div className="card">
        <div className="text-sm font-medium mb-2">📋 今日成交/受阻({plan.account_date || plan.signal_date})</div>
        {plan.executed.length === 0 ? (
          <div className="text-[13px] text-subink">
            {isStale ? `历史模拟盘无成交记录(${plan.action || "—"}),非现行可执行。` : `今日无成交(${plan.action || "—"})。`}
          </div>
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-subink text-left border-b border-cardline">
                <th className="py-1 font-medium">方向</th>
                <th className="py-1 font-medium">代码</th>
                <th className="py-1 font-medium">名称</th>
                <th className="py-1 font-medium text-right">股数</th>
                <th className="py-1 font-medium text-right">成交价</th>
                <th className="py-1 font-medium text-right">金额</th>
                <th className="py-1 font-medium text-right">费用</th>
              </tr>
            </thead>
            <tbody>
              {plan.executed.map((t, i) => (
                <tr key={`${t.code}-${i}`} className="border-b border-cardline/60">
                  <td className="py-1"><SideTag side={t.side} /></td>
                  <td className="py-1 text-ink">{t.code}</td>
                  <td className="py-1 text-ink">{t.name}</td>
                  <td className="py-1 text-right text-subink">{t.shares}</td>
                  <td className="py-1 text-right text-subink">{t.price.toFixed(3)}</td>
                  <td className="py-1 text-right text-ink">{fmt(t.notional)}</td>
                  <td className="py-1 text-right text-subink">{t.cost.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {plan.blocked.length > 0 && (
          <div className="mt-3">
            <div className="text-[12px] font-medium text-warn mb-1">⛔ 未成交 {plan.blocked.length} 笔(真实盘约束)</div>
            {plan.blocked.map((b, i) => (
              <div key={i} className="text-[12px] text-subink">
                {b.side} {b.code} {b.name} —— {b.reason}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 明日计划 */}
      <div className="card">
        <div className="text-sm font-medium mb-1">
          {isStale ? `历史计划(${plan.signal_date} 信号已过期)` : `📅 明日计划(本日 ${plan.signal_date} 信号 → 次日执行)`}
        </div>
        <div className="text-[11px] text-subink mb-2">
          {isStale
            ? "以下仅为历史模拟盘计划留痕,不得作为当前 T+1 执行依据。"
            : "参考价 = 信号日收盘;实际按 T+1 收盘价模式成交,停牌/一字板会被跳过。"}
        </div>
        {plan.plan.length === 0 ? (
          <div className="text-[13px] text-subink">
            股票腿无调仓{bond?.active ? (isStale ? "(历史轮动状态见上方卡片)" : "(轮动指令见上方卡片)") : ",空仓观望"}。
          </div>
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-subink text-left border-b border-cardline">
                <th className="py-1 font-medium">动作</th>
                <th className="py-1 font-medium">代码</th>
                <th className="py-1 font-medium">名称</th>
                <th className="py-1 font-medium text-right">预计股数</th>
                <th className="py-1 font-medium text-right">参考价</th>
                <th className="py-1 font-medium text-right">预计金额</th>
              </tr>
            </thead>
            <tbody>
              {plan.plan.map((p, i) => (
                <tr key={`${p.code}-${i}`} className="border-b border-cardline/60">
                  <td className="py-1"><SideTag side={p.action} /></td>
                  <td className="py-1 text-ink">{p.code}</td>
                  <td className="py-1 text-ink">{p.name}</td>
                  <td className="py-1 text-right text-subink">{p.est_shares}</td>
                  <td className="py-1 text-right text-subink">{p.ref_price.toFixed(3)}</td>
                  <td className="py-1 text-right text-ink">{fmt(p.est_notional)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 当前持仓 */}
      {plan.positions.length > 0 && (
        <div className="card">
          <div className="text-sm font-medium mb-2">📦 当前持仓</div>
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-subink text-left border-b border-cardline">
                <th className="py-1 font-medium">代码</th>
                <th className="py-1 font-medium">名称</th>
                <th className="py-1 font-medium text-right">数量</th>
                <th className="py-1 font-medium text-right">成本</th>
                <th className="py-1 font-medium text-right">现价</th>
                <th className="py-1 font-medium text-right">市值</th>
                <th className="py-1 font-medium text-right">浮盈</th>
              </tr>
            </thead>
            <tbody>
              {plan.positions.map((d) => (
                <tr key={d.code} className="border-b border-cardline/60">
                  <td className="py-1 text-ink">{d.code}{d.asset === "etf" && <span className="ml-1 text-[10px] text-subink">ETF</span>}</td>
                  <td className="py-1 text-ink">{d.name}</td>
                  <td className="py-1 text-right text-subink">{d.shares}</td>
                  <td className="py-1 text-right text-subink">{d.cost.toFixed(3)}</td>
                  <td className="py-1 text-right text-subink">{d.price ? d.price.toFixed(3) : "停牌"}</td>
                  <td className="py-1 text-right text-ink">{fmt(d.mv)}</td>
                  <td className={`py-1 text-right ${d.pnl >= 0 ? "text-ok" : "text-danger"}`}>{d.pnl >= 0 ? "+" : ""}{fmt(d.pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="text-[11px] text-subink">{plan.disclaimer}</div>
    </div>
  );
}
