/**
 * Lab-rail renderer logic (ADR-037 Dual-Rail Evidence), pure functions only.
 *
 * Invariants enforced here (unit-tested adversarially):
 * 1. Every lab turn is tagged rail:"lab" + nonEvidence:true — callers cannot override.
 * 2. Promotion to Strict rail carries the user's hypothesis TEXT only; any token
 *    that looks like a lab artifact path is stripped (params cross, artifacts never).
 * 3. Strict envelopes whose sources touch scratch/ paths are not displayable
 *    (renderer-side wash guard, fail-closed).
 */

const LAB_RAIL = "lab";
const LAB_PATH_RE = /\S*scratch[\\/]+lab\S*/g;
const SCRATCH_SOURCE_RE = /scratch[\\/]/;

export function newLabSessionId() {
  const random = Math.random().toString(36).slice(2, 8);
  return `lab-${Date.now()}-${random}`;
}

/** Append a turn; rail/nonEvidence tags always win over caller-provided fields. */
export function appendLabTurn(turns, role, content, extra = {}) {
  const base = Array.isArray(turns) ? turns : [];
  if (typeof content !== "string" || !content.trim()) return base;
  return [
    ...base,
    {
      ...extra,
      role: role === "user" ? "user" : "assistant",
      content,
      rail: LAB_RAIL,
      nonEvidence: true,
    },
  ];
}

/** Build the assistant turn from a labBridge result. Errors stay honest. */
export function labTurnFromResult(result) {
  const text = typeof result?.text === "string" ? result.text.trim() : "";
  const error = typeof result?.error === "string" ? result.error.trim() : "";
  if (result?.ready && text) {
    return { role: "assistant", content: text, rail: LAB_RAIL, nonEvidence: true };
  }
  const message = error || "Lab 沙箱没有返回内容。";
  return {
    role: "assistant",
    content: `Lab 沙箱异常：${message}`,
    rail: LAB_RAIL,
    nonEvidence: true,
    isError: true,
  };
}

/**
 * Promotion payload: the LAST user hypothesis text, with lab artifact paths
 * stripped. Returns "" when there is nothing user-authored to promote.
 */
export function buildPromotionPrompt(turns) {
  if (!Array.isArray(turns)) return "";
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const turn = turns[i];
    if (turn?.role === "user" && typeof turn.content === "string" && turn.content.trim()) {
      return turn.content.replace(LAB_PATH_RE, "").replace(/\s{2,}/g, " ").trim();
    }
  }
  return "";
}

/**
 * Renderer-side wash guard for Strict envelopes (fail-closed):
 * displayable performance requires a non-empty sources[] of strings,
 * none of which touches a scratch/ path.
 */
export function strictEnvelopeDisplayable(envelope) {
  const sources = envelope?.sources;
  if (!Array.isArray(sources) || sources.length === 0) return false;
  for (const source of sources) {
    if (typeof source !== "string" || !source.trim()) return false;
    if (SCRATCH_SOURCE_RE.test(source)) return false;
  }
  return true;
}
