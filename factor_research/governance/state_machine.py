"""策略生命周期状态机(Task 15)。

消除 active/ACTIVE/在册/APPROVED 等平行状态造成的非法组合:用唯一状态枚举 + 合法
转换表,让 model approval / registry status / deployment status 从同一状态派生。

合法生命周期:
    DRAFT → CANDIDATE → VALIDATED → REGISTERED → DEPLOYED
                          │             │            │
                          └→ REJECTED   └→ RETIRED   └→ SUSPENDED
规则:
  · CANDIDATE 不能直接 DEPLOYED(必须先 VALIDATED→REGISTERED)。
  · RETIRED 是终态,不能再 DEPLOYED;需创建新版本。
  · SUSPENDED 可在证据恢复后回 DEPLOYED。
"""
from __future__ import annotations


class IllegalTransition(ValueError):
    """非法状态转换。"""


# 唯一内部枚举(英文);UI 展示映射见 CN_LABELS,业务逻辑禁止比较中文文案。
STATES = (
    "DRAFT", "CANDIDATE", "VALIDATED", "REGISTERED", "DEPLOYED",
    "REJECTED", "RETIRED", "SUSPENDED",
)

# 合法转换表 from -> {允许的 to}
_TRANSITIONS: dict[str, set] = {
    "DRAFT": {"CANDIDATE"},
    "CANDIDATE": {"VALIDATED", "REJECTED"},
    "VALIDATED": {"REGISTERED", "REJECTED"},
    "REGISTERED": {"DEPLOYED", "RETIRED"},
    "DEPLOYED": {"SUSPENDED", "RETIRED"},
    "SUSPENDED": {"DEPLOYED", "RETIRED"},
    "REJECTED": set(),   # 终态
    "RETIRED": set(),    # 终态:不可复活,须新版本
}

# 内部状态 → 中文台账/UI 展示(只用于展示,不参与判断)
CN_LABELS = {
    "CANDIDATE": "候选",
    "REGISTERED": "在册",
    "RETIRED": "退役",
    "DEPLOYED": "已部署",
    "SUSPENDED": "已暂停",
    "VALIDATED": "已验证",
    "DRAFT": "草稿",
    "REJECTED": "已拒绝",
}

# 中文 → 内部状态(注册表历史用中文存 status,需双向映射)
CN_TO_STATE = {v: k for k, v in CN_LABELS.items()}


def is_terminal(state: str) -> bool:
    return state in {"REJECTED", "RETIRED"}


def can_transition(from_state: str, to_state: str) -> bool:
    if from_state not in _TRANSITIONS:
        return False
    return to_state in _TRANSITIONS[from_state]


def assert_transition(from_state: str, to_state: str) -> None:
    if from_state not in STATES:
        raise IllegalTransition(f"未知起始状态 {from_state!r}")
    if to_state not in STATES:
        raise IllegalTransition(f"未知目标状态 {to_state!r}")
    if not can_transition(from_state, to_state):
        raise IllegalTransition(
            f"非法转换 {from_state} → {to_state}"
            f"(合法: {sorted(_TRANSITIONS.get(from_state, set())) or '终态,无后继'})")
