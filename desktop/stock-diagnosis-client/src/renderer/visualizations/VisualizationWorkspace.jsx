function toneForStatus(status) {
  if (status === "done" || status === "谨慎持有") return "ready";
  if (status === "blocked" || status === "错误" || status === "数据阻断") return "blocked";
  if (status === "pending" || status === "等待输入") return "pending";
  return "neutral";
}

function trustTone(status) {
  if (status === "ready") return "ready";
  if (status === "blocked") return "blocked";
  if (status === "attention") return "pending";
  return "neutral";
}

function asList(value) {
  return Array.isArray(value) ? value : [];
}

export default function VisualizationWorkspace({
  diagnosis,
  runtime,
  onBackToConversation,
  onRequestFormalBacktest,
  capabilityBusy = false,
}) {
  const evidence = asList(diagnosis.evidence);
  const risks = asList(diagnosis.risks);
  const steps = asList(diagnosis.taskSteps);
  const chips = asList(diagnosis.sourceChips);
  const limits = asList(diagnosis.limits);
  const hasStock = Boolean(diagnosis.thread?.code);
  const trust = diagnosis.trust || null;
  const isStrategy = Boolean(
    trust
    || diagnosis.activeSkills?.some((skill) => skill?.mode === "strategy_precheck")
    || chips.includes("strategy-precheck")
  );
  const canRequestBacktest = Boolean(isStrategy && typeof onRequestFormalBacktest === "function");
  const readServiceAvailable = runtime?.readService?.available;
  const readServiceTone = readServiceAvailable === false ? "blocked" : readServiceAvailable === true ? "ready" : "pending";
  const readServiceLabel = readServiceAvailable === false
    ? "read service offline"
    : readServiceAvailable === true
      ? "read service ready"
      : "checking read service";
  const cliCalls = asList(diagnosis.agentTrace?.cliCalls);
  const bannerStatus = trust?.banner_status || (isStrategy ? "attention" : "neutral");
  const bannerTitle = trust?.headline
    || (isStrategy ? "策略想法尚未经确定性回测与 9-Gate" : "证据来自本地只读读模型");
  const bannerDetail = trust?.detail
    || "对话可自然推进；产品结论只使用 CLI/读模型已确认字段。";

  return (
    <section className="visualization-workspace" data-testid="visualization-workspace" aria-label="图形化展示">
      <div className="visual-header">
        <div>
          <div className="workspace-kicker">图形化展示</div>
          <div className="workspace-title">{hasStock ? `${diagnosis.thread.name} ${diagnosis.thread.code}` : (diagnosis.thread?.name || "等待诊断对象")}</div>
          <div className="workspace-subtitle">这里只展示本地后端已经确认的诊断证据和任务状态，不展示伪造曲线。</div>
        </div>
        <div className="visual-header-actions">
          {canRequestBacktest && (
            <button
              className="secondary-button"
              type="button"
              data-testid="request-formal-backtest"
              disabled={capabilityBusy}
              onClick={onRequestFormalBacktest}
            >
              {capabilityBusy ? "执行中…" : "正式回测（需确认）"}
            </button>
          )}
          <button className="secondary-button" type="button" onClick={onBackToConversation}>
            回到对话
          </button>
        </div>
      </div>

      <div className={`trust-banner ${trustTone(bannerStatus)}`} data-testid="trust-banner">
        <div className="trust-banner-top">
          <span className={`state-pill ${trustTone(bannerStatus)}`}>{bannerStatus}</span>
          <span className="trust-kicker">信任校准 · 系统不会用假结论欺骗你</span>
        </div>
        <div className="trust-headline">{bannerTitle}</div>
        <div className="trust-detail">{bannerDetail}</div>
        <div className="trust-flags">
          <span className="source-token">can_claim_valid={String(trust?.can_claim_valid ?? false)}</span>
          <span className="source-token">fake_curve={String(trust?.fake_curve_allowed ?? false)}</span>
          {trust?.evidence_tier ? <span className="source-token">tier={trust.evidence_tier}</span> : null}
          {trust?.protocol_id ? <span className="source-token">protocol={trust.protocol_id}</span> : null}
          <span className="source-token">perf_display={String(Boolean(trust?.allows_performance_display))}</span>
          {trust?.cost_display ? <span className="source-token">成本 {trust.cost_display}</span> : null}
          {trust?.validation_status ? <span className="source-token">{trust.validation_status}</span> : null}
        </div>
      </div>

      <div className="viz-metrics" aria-label="当前诊断指标">
        <div className="viz-metric">
          <div className="metric-label">对象</div>
          <div className="metric-value">{hasStock ? diagnosis.thread.code : (isStrategy ? "策略想法" : "未选择")}</div>
        </div>
        <div className="viz-metric">
          <div className="metric-label">结论</div>
          <div className="metric-value">{diagnosis.decision?.verdict || "等待输入"}</div>
        </div>
        <div className="viz-metric">
          <div className="metric-label">证据</div>
          <div className="metric-value">{evidence.length}</div>
        </div>
        <div className="viz-metric">
          <div className="metric-label">CLI 调用</div>
          <div className="metric-value">{cliCalls.length}</div>
        </div>
      </div>

      <div className="viz-grid">
        <div className="viz-panel">
          <div className="viz-panel-header">
            <div>
              <div className="viz-title">诊断链路</div>
              <div className="viz-subtitle">来自 Electron 主进程和本地 Python 能力的任务状态。</div>
            </div>
            <span className={`state-pill ${readServiceTone}`}>
              {readServiceLabel}
            </span>
          </div>
          <div className="pipeline-map">
            {steps.map((step) => (
              <div className="pipeline-step" key={step.name}>
                <div className={`pipeline-node ${toneForStatus(step.status)}`}>{step.status === "done" ? "✓" : step.status === "blocked" ? "!" : "·"}</div>
                <div>
                  <div className="pipeline-name">{step.name}</div>
                  <div className="pipeline-desc">{step.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="viz-panel">
          <div className="viz-panel-header">
            <div>
              <div className="viz-title">证据地图</div>
              <div className="viz-subtitle">按当前诊断返回内容展开，数量和文本都来自后端。</div>
            </div>
          </div>
          <div className="source-map">
            {chips.map((chip) => (
              <span className="source-token" key={chip}>{chip}</span>
            ))}
          </div>
          <ul className="viz-list">
            {evidence.slice(0, 8).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      </div>

      <div className="viz-grid">
        <div className="viz-panel">
          <div className="viz-panel-header">
            <div>
              <div className="viz-title">{isStrategy ? "定义缺口与风险" : "风险摘要"}</div>
              <div className="viz-subtitle">当前只做证据归纳，不输出买卖指令。</div>
            </div>
          </div>
          <ul className="viz-list">
            {(trust?.missing_fields?.length
              ? trust.missing_fields.map((field) => `缺: ${field}`)
              : risks
            ).slice(0, 6).map((risk) => (
              <li key={risk}>{risk}</li>
            ))}
          </ul>
        </div>

        <div className="viz-panel boundary-panel" data-testid="honesty-boundary-panel">
          <div className="viz-panel-header">
            <div>
              <div className="viz-title">{isStrategy ? "诚实边界 · 策略验证" : "能力边界"}</div>
              <div className="viz-subtitle">用户可见的系统不欺骗承诺</div>
            </div>
            <span className="state-pill pending">{isStrategy ? "预检" : "只读"}</span>
          </div>
          {isStrategy ? (
            <>
              <p>
                自然语言可以像 Codex 一样讨论和推进想法，但产品层强制：
                不宣布有效、不生成伪收益曲线、不把相关家族线索当成 alpha。
                正式有效性只认 BacktestEngine + 9-Gate + 台账证据。
              </p>
              <ul className="viz-list">
                {limits.slice(0, 5).map((item) => (
                  <li key={item}>{item}</li>
                ))}
                {cliCalls.map((call) => (
                  <li key={`${call.capability}-${JSON.stringify(call.arguments || {})}`}>
                    CLI {call.isError ? "失败" : "成功"}: {call.capability || "unknown"}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p>
              个股诊断结论来自本地只读画像。Agent 只能解释证据，不能替代确定性读模型，也不连接交易执行。
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
