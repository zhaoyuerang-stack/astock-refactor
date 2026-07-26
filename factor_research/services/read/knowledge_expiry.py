"""Knowledge-Expiry Read Service —— 研究结论保质期复核(REQUIRE_RETEST 环的读层)。

回答唯一问题:**哪些研究结论已过保质期,需要人决定「复测还是续期」?**

背景(孤岛回收,ADR-034 后续):`knowledge.graph.Finding` 自带 expires 保质期与
`check_expiry()`(含父结论过期级联),设计初衷是「避免已解决的问题成为探索的坟墓」;
但 check_expiry 此前只挂在 factory_cli 手动命令里——结论过期了没人重测,
保质期语义落空一半。本服务把两类过期机械透出:
  ① findings.json 机器自长结论(check_expiry:自身过期 + 父过期级联);
  ② direction_registry.json 方向条目(过期 = 自动失效 = 该按 revival_condition 复活重测)。

边界:纯读层 advisory——过期只是「该重测」的信号,重测执行(probe/L0 重跑)与
续期(改 expires 须带新证据)归人;本服务不改任何文件,不做有效性判断(R-LLM-001)。
"""
from __future__ import annotations


def get_knowledge_expiry(
    *,
    store_path: str | None = None,
    registry_path: str | None = None,
) -> dict:
    """过期结论清单。state: retest_due(有过期) / fresh(无) 。"""
    from knowledge.directions import load_direction_entries
    from knowledge.graph import load_graph

    # include_directions=False:方向条目的过期走 ② 单独点名(load_graph 只合并活跃条目,
    # 过期方向 findings 根本不会出现在图里,靠图查过期会漏)
    kg = load_graph(store_path, include_directions=False) if store_path \
        else load_graph(include_directions=False)
    expired_findings = [
        {"id": f.id, "statement": f.statement, "domain": f.domain, "expires": f.expires}
        for f in kg.check_expiry()
    ]
    expired_directions = [
        {"id": e.id, "direction": e.direction, "expires": e.expires,
         "revival_condition": e.revival_condition or "(未写复活条件——重测前先补)"}
        for e in load_direction_entries(registry_path) if not e.is_active
    ]

    n = len(expired_findings) + len(expired_directions)
    return {
        "state": "retest_due" if n else "fresh",
        "n_expired": n,
        "expired_findings": expired_findings,
        "expired_directions": expired_directions,
        "honesty": "advisory:过期=失效(gate 已停止生效),该重测或带新证据续期;"
                   "重测执行与续期归人,本服务零写入、不判有效(R-LLM-001)。",
    }
