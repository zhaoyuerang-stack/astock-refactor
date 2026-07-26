import { useEffect, useRef, useState } from "react";
import skillDefinitions from "../shared/skills.json";
import VisualizationWorkspace from "./visualizations/VisualizationWorkspace.jsx";
import {
  appendLabTurn,
  buildPromotionPrompt,
  labTurnFromResult,
  newLabSessionId,
  strictEnvelopeDisplayable,
} from "./lab/labLogic.mjs";

const initialDiagnosis = {
  thread: { id: "empty", name: "等待输入", code: "", status: "等待输入" },
  taskSteps: [
    { name: "理解问题", desc: "等待输入股票、持仓问题或策略想法。", status: "pending" },
    { name: "读取系统能力", desc: "Pi agent 提交后调用本地 readonly CLI。", status: "pending" },
    { name: "读取风险快照", desc: "汇总真实股价、估值、收益和资金流。", status: "pending" },
    { name: "生成保守诊断", desc: "只使用 CLI 返回的已检查证据。", status: "pending" },
  ],
  decision: {
    verdict: "等待输入",
    note: "还没有读取任何股票画像。",
    summary: "输入股票名或 6 位代码后，Pi agent 会读取系统 CLI，再由确定性代码整理证据。",
    notHeld: "先输入目标股票，再进入观察池判断。",
    held: "先读取本地画像，再讨论持有风险。",
  },
  risks: ["尚未选择股票。"],
  evidence: ["尚未读取本地数据。"],
  limits: [
    "本结果不构成交易建议。",
    "客户端只读本地数据，不连接交易执行。",
  ],
  sourceChips: ["等待输入", "read-only"],
  activeSkills: [],
  piExplanation: "",
  turns: [],
};

const skillById = new Map(skillDefinitions.map((skill) => [skill.id, skill]));

function publicSkill(skill) {
  if (!skill) return null;
  return {
    id: skill.id,
    name: skill.name,
    shortName: skill.shortName,
    category: skill.category,
    description: skill.description,
    boundary: skill.boundary,
    requiresStock: Boolean(skill.requiresStock),
    mode: skill.mode,
  };
}

function statusClass(status) {
  if (status === "谨慎持有") return "hold";
  if (status === "等待输入") return "waiting";
  if (status === "错误" || status === "数据阻断") return "error";
  if (status === "数据不足" || status === "待模拟盘" || status === "想法预检") return "insufficient";
  if (status === "处理中") return "waiting";
  return "observe";
}

function unavailableDiagnosis(prompt, message, selectedSkill = null) {
  const activeSkills = selectedSkill ? [publicSkill(selectedSkill)] : [];
  return {
    thread: { id: `error-${Date.now()}`, name: "本地能力不可用", code: "", status: "错误" },
    taskSteps: [
      { name: "识别股票", desc: "请求已收到。", status: "done" },
      { name: "调用 Pi 与系统 CLI", desc: "Agent CLI 与本地 read service 均未返回可用证据。", status: "blocked" },
      { name: "读取风险快照", desc: "未读取到真实数据。", status: "pending" },
      { name: "生成保守诊断", desc: "已停止，避免用假数据填充。", status: "blocked" },
    ],
    decision: {
      verdict: "错误",
      note: "本地 Agent 能力不可用。",
      summary: `用户问题：“${prompt}”。客户端没有拿到真实数据，因此不会展示 demo 结论。错误: ${message}`,
      notHeld: "先不要进入候选。",
      held: "先不要按当前失败结果调整仓位。",
    },
    risks: ["未读取到 Pi CLI 或本地 read service 证据。", "当前界面没有使用假数据兜底。"],
    evidence: [...(selectedSkill ? [`选中 Skill: ${selectedSkill.name}`] : []), `用户输入: ${prompt}`, `错误: ${message}`],
    limits: ["需要 Pi CLI 或本地 read service 至少一条读取路径可用。", ...(selectedSkill ? [`Skill 边界: ${selectedSkill.boundary}`] : [])],
    sourceChips: [...(selectedSkill ? [`Skill: ${selectedSkill.shortName || selectedSkill.name}`] : []), "offline", "no-demo"],
    activeSkills,
    piExplanation: "",
    turns: [{ role: "user", content: prompt }, { role: "assistant", content: `本地能力不可用: ${message}` }],
  };
}

function browserPreviewDiagnosis(prompt, selectedSkill = null) {
  return unavailableDiagnosis(prompt, "未连接 Electron preload，请通过 AStock Lens.app 打开。", selectedSkill);
}

function structuredIpcAvailable(runtime) {
  const preloadVersion = Number(window.astock?.apiVersion || 0);
  const runtimeVersion = Number(runtime?.apiVersion || 0);
  return Math.max(preloadVersion, runtimeVersion) >= 2;
}

function legacyDiagnosisPrompt(text, diagnosis) {
  const code = diagnosis.thread?.code || "";
  if (!code || text.includes(code)) return text;
  return `${code} ${text}`;
}

function shouldKeepWorkspace(diagnosis) {
  return Boolean(diagnosis?.thread?.id && diagnosis.thread.id !== initialDiagnosis.thread.id);
}

function diagnosisContext(diagnosis, selectedSkill) {
  const base = { selectedSkillId: selectedSkill?.id || "" };
  if (!shouldKeepWorkspace(diagnosis) && !(diagnosis.turns || []).length) return base;
  return {
    ...base,
    currentThread: diagnosis.thread,
    turns: diagnosis.turns || [],
  };
}

function keepActiveWorkspace(result, activeDiagnosis) {
  if (!shouldKeepWorkspace(activeDiagnosis)) return result;
  return {
    ...result,
    thread: {
      ...result.thread,
      id: activeDiagnosis.thread.id,
    },
  };
}

function pendingDiagnosis(diagnosis, text, selectedSkill) {
  const skillName = selectedSkill?.name || "默认诊断";
  return {
    ...diagnosis,
    thread: {
      ...diagnosis.thread,
      status: "处理中",
    },
    taskSteps: [
      {
        name: "Pi agent 执行中",
        desc: `Pi agent 正在按 ${skillName} 通过系统 CLI 读取事实。`,
        status: "pending",
      },
      ...(diagnosis.taskSteps || []),
    ],
    turns: [
      ...(diagnosis.turns || []),
      { role: "user", content: text },
      {
        role: "assistant",
        content: "Pi agent 正在调用系统 CLI 读取证据…",
        pending: true,
      },
    ],
  };
}

function sameThreadIdentity(left = {}, right = {}) {
  if (left.id && right.id && left.id === right.id) return true;
  if (left.code && right.code && left.code === right.code) return true;
  return false;
}

function upsertDiagnosisList(items, diagnosis, replacedThread = null) {
  return [
    diagnosis,
    ...items.filter((item) => (
      !sameThreadIdentity(item.thread, diagnosis.thread)
      && !(replacedThread && sameThreadIdentity(item.thread, replacedThread))
    )),
  ];
}

function upsertThreadList(items, thread, replacedThread = null) {
  return [
    thread,
    ...items.filter((item) => (
      !sameThreadIdentity(item, thread)
      && !(replacedThread && sameThreadIdentity(item, replacedThread))
    )),
  ];
}

function dedupeThreadList(items, preferredId = "") {
  return items.reduce((result, thread) => {
    const existingIndex = result.findIndex((item) => sameThreadIdentity(item, thread));
    if (existingIndex === -1) return [...result, thread];
    if (thread.id === preferredId) {
      return result.map((item, index) => (index === existingIndex ? thread : item));
    }
    return result;
  }, []);
}

function evidenceLines(items = [], limit = 3) {
  return items
    .filter(Boolean)
    .slice(0, limit)
    .map((item) => `- ${item}`)
    .join("\n");
}

function diagnosisConversationReply(diagnosis) {
  if (!shouldKeepWorkspace(diagnosis) && diagnosis.decision?.verdict === "等待输入") return "";
  if (diagnosis.piExplanation) return diagnosis.piExplanation;

  const decision = diagnosis.decision || {};
  const thread = diagnosis.thread || {};
  const objectName = thread.code ? `${thread.name} ${thread.code}` : thread.name || "当前对象";
  const checked = evidenceLines((diagnosis.risks || []).length ? diagnosis.risks : diagnosis.evidence);

  if (!thread.code) {
    return `${decision.summary || "还没有识别到股票代码。"}\n\n你可以继续补充股票名称或 6 位代码，我会在同一个工作空间里继续。`;
  }

  return `${objectName} 已完成本地诊断。\n\n${decision.summary || decision.note || "当前只展示已读取证据。"}\n\n已检查:\n${checked || "- 当前证据不足。"}\n\n继续追问时，我会沿用这个对象和当前证据边界。`;
}

function conversationTurnsForDisplay(diagnosis) {
  const turns = Array.isArray(diagnosis.turns)
    ? diagnosis.turns.filter((turn) => turn && typeof turn.content === "string")
    : [];
  if (turns.length) return turns;

  const reply = diagnosisConversationReply(diagnosis);
  return reply ? [{ role: "assistant", content: reply, synthesized: true }] : [];
}

function ensureResultTurns(result, pending) {
  if (Array.isArray(result.turns) && result.turns.length) return result;
  const pendingTurns = Array.isArray(pending?.turns)
    ? pending.turns.filter((turn) => turn && !turn.pending)
    : [];
  const reply = diagnosisConversationReply(result);
  return {
    ...result,
    turns: reply ? [...pendingTurns, { role: "assistant", content: reply, synthesized: true }] : pendingTurns,
  };
}

function ThreadSidebar({ threads, activeId, onSelect, onNew }) {
  return (
    <aside className="sidebar" data-testid="thread-sidebar" aria-label="股票诊断线程">
      <div className="brand">
        <div className="brand-mark">AL</div>
        <div>
          <div className="brand-title">AStock Lens</div>
          <div className="brand-subtitle">本地优先 · 对话验证客户端</div>
        </div>
      </div>
      <div className="sidebar-toolbar">
        <button className="new-thread" type="button" onClick={onNew}>
          <span aria-hidden="true">+</span>
          <span>新对象</span>
        </button>
      </div>
      <div className="thread-list">
        {threads.length === 0 && (
          <div className="thread-empty">
            <div>暂无诊断线程</div>
            <div>从底部输入股票名或代码开始。</div>
          </div>
        )}
        {threads.map((thread) => (
          <button
            key={thread.id}
            className={`thread ${thread.id === activeId ? "active" : ""}`}
            type="button"
            onClick={() => onSelect(thread.id)}
          >
            <div className="thread-title-row">
              <span className="thread-name">
                {thread.name} {thread.code}
              </span>
              <span className={`badge ${statusClass(thread.status)}`}>{thread.status}</span>
            </div>
            <div className="thread-meta">{thread.updated}</div>
          </button>
        ))}
      </div>
      <div className="sidebar-footer">
        <div>对象线程</div>
        <div className="mono">Local engine · read-only</div>
      </div>
    </aside>
  );
}

function SkillPicker({ skills, selectedSkillId, onSelect, onClose }) {
  return (
    <div className="skill-popover" id="skill-picker" data-testid="skill-picker" role="dialog" aria-label="选择 Skill">
      <div className="skill-popover-header">
        <div>
          <div className="skill-popover-title">选择 Skill</div>
          <div className="skill-popover-subtitle">像插件一样为当前问题附加专业诊断方式。</div>
        </div>
        <button className="skill-close" type="button" aria-label="关闭 Skill 菜单" onClick={onClose}>
          x
        </button>
      </div>
      <div className="skill-list">
        {skills.map((skill) => (
          <button
            className={`skill-option ${selectedSkillId === skill.id ? "active" : ""}`}
            type="button"
            key={skill.id}
            onClick={() => onSelect(skill.id)}
          >
            <span className="skill-option-top">
              <span className="skill-name">{skill.name}</span>
              <span className="skill-category">{skill.category}</span>
            </span>
            <span className="skill-desc">{skill.description}</span>
            <span className="skill-hint">{skill.promptHint}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function ActiveSkillBar({ skill, onClear }) {
  if (!skill) return null;
  return (
    <div className="active-skill-bar" data-testid="active-skill-bar">
      <div>
        <span className="active-skill-label">已启用 Skill</span>
        <span className="active-skill-name">{skill.name}</span>
      </div>
      <button type="button" onClick={onClear}>移除</button>
    </div>
  );
}

function ConversationWorkspace({ diagnosis }) {
  const turns = conversationTurnsForDisplay(diagnosis);
  const hasTurns = turns.length > 0;
  const conversationEndRef = useRef(null);

  useEffect(() => {
    if (hasTurns) {
      conversationEndRef.current?.scrollIntoView({ block: "end" });
    }
  }, [hasTurns, turns.length, turns.at(-1)?.content]);

  return (
    <section className="conversation-workspace" data-testid="conversation-workspace">
      <div className="conversation-flow" data-testid="conversation-history">
        {hasTurns ? (
          turns.map((turn, index) => (
            <div className={`turn ${turn.role} ${turn.pending ? "pending" : ""}`} key={`${turn.role}-${index}-${turn.content}`}>
              <div className="turn-role">{turn.role === "user" ? "你" : "AStock Lens"}</div>
              <div className="turn-content">{turn.content}</div>
            </div>
          ))
        ) : (
          <div className="empty-chat-line">从底部输入股票、持仓问题或策略想法开始。</div>
        )}
        <div ref={conversationEndRef} data-testid="conversation-end" />
      </div>
    </section>
  );
}

function LabWorkspace({ turns, loading, onPromote }) {
  const endRef = useRef(null);
  const hasUserTurn = turns.some((turn) => turn.role === "user");

  useEffect(() => {
    if (turns.length) endRef.current?.scrollIntoView({ block: "end" });
  }, [turns.length]);

  return (
    <section className="lab-workspace" data-testid="lab-workspace">
      <div className="lab-watermark" data-testid="lab-watermark">
        <div className="lab-watermark-text">
          <span className="lab-watermark-tag">Lab 草稿沙箱</span>
          <span className="lab-watermark-copy">
            非证据 · 输出不得视为「已验证 / 可入册 / 可实盘」；写盘被 OS 沙箱限制在 scratch/lab。
          </span>
        </div>
        <button
          type="button"
          className="lab-promote"
          data-testid="lab-promote"
          title="仅把你的假设文本带回 Strict 轨输入框，由你确认发送；Lab 产物不跨轨"
          onClick={onPromote}
          disabled={!hasUserTurn}
        >
          用 Strict 轨复现
        </button>
      </div>
      <div className="conversation-flow lab-flow" data-testid="lab-history">
        {turns.length ? (
          turns.map((turn, index) => (
            <div
              className={`turn ${turn.role} lab-turn ${turn.isError ? "lab-error" : ""}`}
              key={`lab-${turn.role}-${index}-${turn.content.slice(0, 24)}`}
            >
              <div className="turn-role">
                {turn.role === "user" ? "你" : "Lab 沙箱"}
                <span className="lab-badge">非证据</span>
              </div>
              <div className="turn-content">{turn.content}</div>
            </div>
          ))
        ) : (
          <div className="empty-chat-line">
            这里是隔离草稿区：自由探索想法、跑一次性实验；结论要走 Strict 轨复现后才算数。
          </div>
        )}
        {loading && <div className="empty-chat-line">Lab 沙箱执行中…</div>}
        <div ref={endRef} />
      </div>
    </section>
  );
}

function ConfirmModal({ open, title, body, onConfirm, onCancel, busy }) {
  if (!open) return null;
  return (
    <div className="confirm-modal-backdrop" data-testid="mid-confirm-modal" role="dialog" aria-modal="true">
      <div className="confirm-modal">
        <div className="confirm-modal-title">{title}</div>
        <div className="confirm-modal-body">{body}</div>
        <div className="confirm-modal-actions">
          <button type="button" className="secondary-button" onClick={onCancel} disabled={busy}>
            取消
          </button>
          <button
            type="button"
            className="send-button"
            data-testid="mid-confirm-accept"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "执行中…" : "确认执行"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Workspace({
  diagnosis,
  prompt,
  setPrompt,
  onSubmit,
  loading,
  inputRef,
  runtime,
  viewMode,
  setViewMode,
  skills,
  selectedSkill,
  selectedSkillId,
  onSelectSkill,
  onClearSkill,
  onRequestFormalBacktest,
  capabilityBusy,
  confirmState,
  onConfirmAccept,
  onConfirmCancel,
  labTurns,
  labLoading,
  onPromoteLab,
}) {
  const [skillMenuOpen, setSkillMenuOpen] = useState(false);
  const labMode = viewMode === "lab";
  const composerBusy = labMode ? labLoading : loading;
  const piCliReady = runtime?.pi?.available === true
    && runtime.pi.cliAvailable === true
    && runtime.pi.modelAvailable === true;
  const readServiceReady = runtime?.readService?.available === true;
  const runtimeOffline = Boolean(runtime) && !piCliReady && !readServiceReady;
  const runtimeLabel = !runtime
    ? "Checking local runtime"
    : piCliReady
      ? "Pi agent · CLI mode"
      : readServiceReady
        ? "Read service fallback"
        : "Local runtime offline";

  return (
    <main className="workspace" data-testid="diagnosis-workspace">
      <header className="topbar">
        <div>
          <div className="top-title">
            {diagnosis.thread.name} {diagnosis.thread.code}
          </div>
          <div className="top-meta">
            <span>{diagnosis.sourceChips[0] || "数据日期未知"}</span>
            <span>·</span>
            <span>本地诊断</span>
          </div>
        </div>
        <div className="top-actions">
          <div className="view-switch" aria-label="工作区视图">
            <button
              className={viewMode === "conversation" ? "active" : ""}
              type="button"
              aria-pressed={viewMode === "conversation"}
              onClick={() => setViewMode("conversation")}
            >
              对话
            </button>
            <button
              className={viewMode === "visualization" ? "active" : ""}
              data-testid="visualization-entry"
              type="button"
              aria-pressed={viewMode === "visualization"}
              onClick={() => setViewMode("visualization")}
            >
              图形化展示
            </button>
            <button
              className={`lab-switch ${labMode ? "active" : ""}`}
              data-testid="lab-entry"
              type="button"
              aria-pressed={labMode}
              onClick={() => setViewMode("lab")}
            >
              Lab 草稿
            </button>
          </div>
          <div className="top-meta">
            <span className={`status-dot ${runtimeOffline ? "offline" : ""}`} aria-hidden="true"></span>
            <span>{runtimeLabel}</span>
          </div>
        </div>
      </header>
      <section className="workspace-scroll" aria-label="当前诊断任务">
        {viewMode === "visualization" ? (
          <VisualizationWorkspace
            diagnosis={diagnosis}
            runtime={runtime}
            onBackToConversation={() => setViewMode("conversation")}
            onRequestFormalBacktest={onRequestFormalBacktest}
            capabilityBusy={capabilityBusy}
          />
        ) : labMode ? (
          <LabWorkspace turns={labTurns} loading={labLoading} onPromote={onPromoteLab} />
        ) : (
          <div className="conversation-layout">
            <ConversationWorkspace diagnosis={diagnosis} />
          </div>
        )}
      </section>
      <ConfirmModal
        open={Boolean(confirmState?.open)}
        title="确认正式回测"
        body={confirmState?.prompt || ""}
        busy={capabilityBusy}
        onConfirm={onConfirmAccept}
        onCancel={onConfirmCancel}
      />
      <footer className="composer-shell" data-testid="bottom-composer">
        <div className="composer-stack">
          {skillMenuOpen && (
            <SkillPicker
              skills={skills}
              selectedSkillId={selectedSkillId}
              onSelect={(skillId) => {
                onSelectSkill(skillId);
                setSkillMenuOpen(false);
                inputRef.current?.focus();
              }}
              onClose={() => setSkillMenuOpen(false)}
            />
          )}
          <ActiveSkillBar
            skill={selectedSkill}
            onClear={() => {
              onClearSkill();
              setSkillMenuOpen(false);
              inputRef.current?.focus();
            }}
          />
          <form className="composer" onSubmit={onSubmit}>
            <button
              className={`icon-button ${selectedSkill ? "active" : ""}`}
              data-testid="composer-skill-button"
              type="button"
              title="添加 Skill"
              aria-label="添加 Skill"
              aria-expanded={skillMenuOpen}
              aria-controls="skill-picker"
              onClick={() => setSkillMenuOpen((open) => !open)}
            >
              +
            </button>
            <input
              ref={inputRef}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder={labMode
                ? "Lab 草稿：自由探索想法或实验（输出永远是非证据）…"
                : selectedSkill ? `${selectedSkill.name}: ${selectedSkill.promptHint}` : "问股票，或直接说策略想法（如：用 WACC 做因子试策略）…"}
              aria-label={labMode ? "Lab 草稿输入" : "诊断输入"}
            />
            <button className="send-button" type="submit" disabled={composerBusy}>
              {composerBusy ? (labMode ? "探索中" : "诊断中") : "发送"}
            </button>
          </form>
        </div>
      </footer>
    </main>
  );
}

function appendCapabilityTurns(diagnosis, userLine, assistantLine) {
  return {
    ...diagnosis,
    turns: [
      ...(diagnosis.turns || []),
      { role: "user", content: userLine },
      { role: "assistant", content: assistantLine },
    ],
  };
}

function formatBacktestAssistantMessage(result) {
  const envelope = result?.evidence_envelope || result?.result?.evidence_envelope;
  const payload = result?.result || {};
  const tier = envelope?.evidence_tier || "engine";
  if (!result?.ok) {
    return `正式回测未执行或失败：${result?.error || "未知错误"}。can_claim_valid=false。`;
  }
  const annual = payload.annual;
  const sharpe = payload.sharpe;
  const maxdd = payload.maxdd;
  const lines = [
    `正式回测完成（evidence_tier=${tier}，can_claim_valid=false，非入册裁决）。`,
    typeof annual === "number" ? `年化(引擎): ${(annual * 100).toFixed(2)}%` : null,
    typeof sharpe === "number" ? `夏普(引擎): ${sharpe.toFixed(3)}` : null,
    typeof maxdd === "number" ? `最大回撤(引擎): ${(maxdd * 100).toFixed(2)}%` : null,
    "成本口径为引擎固定 CostModel，不可调低。",
  ].filter(Boolean);
  return lines.join("\n");
}

export default function App() {
  const [diagnoses, setDiagnoses] = useState([initialDiagnosis]);
  const [threads, setThreads] = useState([]);
  const [activeId, setActiveId] = useState(initialDiagnosis.thread.id);
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [runtime, setRuntime] = useState(null);
  const [viewMode, setViewMode] = useState("conversation");
  const [selectedSkillId, setSelectedSkillId] = useState("");
  const [capabilityBusy, setCapabilityBusy] = useState(false);
  const [confirmState, setConfirmState] = useState(null);
  const [labTurns, setLabTurns] = useState([]);
  const [labSessionId, setLabSessionId] = useState("");
  const [labLoading, setLabLoading] = useState(false);
  const inputRef = useRef(null);

  const activeDiagnosis = diagnoses.find((item) => item.thread.id === activeId) || initialDiagnosis;
  const visibleThreads = dedupeThreadList(threads, activeId);
  const selectedSkill = skillById.get(selectedSkillId) || null;

  useEffect(() => {
    window.astock?.getRuntimeStatus?.().then(setRuntime).catch(() => undefined);
  }, []);

  async function requestFormalBacktest() {
    if (!window.astock?.runCapability || capabilityBusy || loading) return;
    setCapabilityBusy(true);
    try {
      const preview = await window.astock.runCapability({
        tool: "run_backtest",
        args: {},
        confirmed: false,
      });
      if (preview?.needs_confirmation) {
        setConfirmState({
          open: true,
          tool: "run_backtest",
          args: preview.args || {},
          prompt: preview.confirm_prompt || "确认执行正式回测？",
        });
        return;
      }
      if (!preview?.ok) {
        const updated = appendCapabilityTurns(
          activeDiagnosis,
          "请求正式回测",
          `无法启动正式回测：${preview?.error || "能力不可用"}`,
        );
        setDiagnoses((prev) => upsertDiagnosisList(prev, updated));
        setViewMode("conversation");
      }
    } finally {
      setCapabilityBusy(false);
    }
  }

  async function acceptFormalBacktest() {
    if (!confirmState?.open || !window.astock?.runCapability) return;
    setCapabilityBusy(true);
    try {
      const result = await window.astock.runCapability({
        tool: confirmState.tool || "run_backtest",
        args: confirmState.args || {},
        confirmed: true,
      });
      setConfirmState(null);
      const message = formatBacktestAssistantMessage(result);
      const updated = appendCapabilityTurns(activeDiagnosis, "确认正式回测", message);
      if (result?.ok && result.evidence_envelope && strictEnvelopeDisplayable(result.evidence_envelope)) {
        updated.trust = {
          ...(updated.trust || {}),
          can_claim_valid: false,
          fake_curve_allowed: false,
          evidence_tier: result.evidence_envelope.evidence_tier || "engine",
          protocol_id: result.evidence_envelope.protocol_id || "engine_backtest",
          sources: result.evidence_envelope.sources || ["tool:run_backtest"],
          allows_performance_display: true,
        };
        updated.evidenceEnvelope = result.evidence_envelope;
      }
      setDiagnoses((prev) => upsertDiagnosisList(prev, updated));
      setViewMode("conversation");
    } finally {
      setCapabilityBusy(false);
    }
  }

  async function submitLab(text) {
    const sessionId = labSessionId || newLabSessionId();
    if (!labSessionId) setLabSessionId(sessionId);
    setLabLoading(true);
    setLabTurns((prev) => appendLabTurn(prev, "user", text));
    try {
      const result = window.astock?.runLabTurn
        ? await window.astock.runLabTurn({ prompt: text, sessionId })
        : { ready: false, error: "未连接 Electron preload，请通过 AStock Lens.app 打开。" };
      setLabTurns((prev) => [...prev, labTurnFromResult(result)]);
    } catch (error) {
      setLabTurns((prev) => [
        ...prev,
        labTurnFromResult({ ready: false, error: error?.message || String(error) }),
      ]);
    } finally {
      setLabLoading(false);
    }
  }

  function promoteLabToStrict() {
    const promoted = buildPromotionPrompt(labTurns);
    if (!promoted) return;
    setViewMode("conversation");
    setPrompt(promoted);
    inputRef.current?.focus();
  }

  async function submit(event) {
    event.preventDefault();
    const text = prompt.trim();
    if (!text) return;
    if (viewMode === "lab") {
      if (labLoading) return;
      setPrompt("");
      await submitLab(text);
      return;
    }
    if (loading) return;
    let pending = null;
    setLoading(true);
    try {
      const context = diagnosisContext(activeDiagnosis, selectedSkill);
      pending = pendingDiagnosis(activeDiagnosis, text, selectedSkill);
      setDiagnoses((prev) => upsertDiagnosisList(prev, pending));
      setThreads((prev) => upsertThreadList(prev, {
        ...pending.thread,
        updated: "刚刚",
      }));
      setActiveId(pending.thread.id);
      setViewMode("conversation");
      setPrompt("");
      const rawResult = window.astock?.runDiagnosis
        ? await window.astock.runDiagnosis(
            structuredIpcAvailable(runtime)
              ? { prompt: text, context }
              : legacyDiagnosisPrompt(text, activeDiagnosis)
          )
        : browserPreviewDiagnosis(text, selectedSkill);
      const result = ensureResultTurns(keepActiveWorkspace(rawResult, activeDiagnosis), pending);
      setDiagnoses((prev) => upsertDiagnosisList(prev, result, pending?.thread));
      setThreads((prev) => upsertThreadList(prev, {
        ...result.thread,
        updated: "刚刚",
      }, pending?.thread));
      setActiveId(result.thread.id);
      setViewMode("conversation");
      setSelectedSkillId(result.activeSkills?.[0]?.id || selectedSkill?.id || "");
    } catch (error) {
      const result = ensureResultTurns(
        keepActiveWorkspace(unavailableDiagnosis(text, error?.message || String(error), selectedSkill), activeDiagnosis),
        pending
      );
      if (shouldKeepWorkspace(activeDiagnosis)) {
        const baseTurns = Array.isArray(pending?.turns)
          ? pending.turns.filter((turn) => turn && !turn.pending)
          : [
              ...(activeDiagnosis.turns || []),
              { role: "user", content: text },
            ];
        result.turns = [
          ...baseTurns,
          { role: "assistant", content: `本地能力不可用: ${error?.message || String(error)}` },
        ];
      }
      setDiagnoses((prev) => upsertDiagnosisList(prev, result, pending?.thread));
      setThreads((prev) => upsertThreadList(prev, {
        ...result.thread,
        updated: "刚刚",
      }, pending?.thread));
      setActiveId(result.thread.id);
      setViewMode("conversation");
      setSelectedSkillId(result.activeSkills?.[0]?.id || selectedSkill?.id || "");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app-frame">
      <ThreadSidebar
        threads={visibleThreads}
        activeId={activeId}
        onSelect={(threadId) => {
          setActiveId(threadId);
          setViewMode("conversation");
          const nextDiagnosis = diagnoses.find((item) => item.thread.id === threadId);
          setSelectedSkillId(nextDiagnosis?.activeSkills?.[0]?.id || "");
        }}
        onNew={() => {
          setActiveId(initialDiagnosis.thread.id);
          setViewMode("conversation");
          setSelectedSkillId("");
          setPrompt("");
          inputRef.current?.focus();
        }}
      />
      <Workspace
        diagnosis={activeDiagnosis}
        prompt={prompt}
        setPrompt={setPrompt}
        onSubmit={submit}
        loading={loading}
        inputRef={inputRef}
        runtime={runtime}
        viewMode={viewMode}
        setViewMode={setViewMode}
        skills={skillDefinitions}
        selectedSkill={selectedSkill}
        selectedSkillId={selectedSkillId}
        onSelectSkill={setSelectedSkillId}
        onClearSkill={() => setSelectedSkillId("")}
        onRequestFormalBacktest={requestFormalBacktest}
        capabilityBusy={capabilityBusy}
        confirmState={confirmState}
        onConfirmAccept={acceptFormalBacktest}
        onConfirmCancel={() => setConfirmState(null)}
        labTurns={labTurns}
        labLoading={labLoading}
        onPromoteLab={promoteLabToStrict}
      />
    </div>
  );
}
