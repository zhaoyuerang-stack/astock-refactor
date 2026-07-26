#!/bin/bash
# One-command test runner for the entire project.
set -e

cd "$(dirname "$0")/.."

echo "=== check_layer_deps.py (分层依赖 + 台账唯一写入口) ==="
python3 scripts/ci/check_layer_deps.py

echo ""
echo "=== check_test_discovery.py (全量测试发现:防 test_*.py 被静默排除) ==="
python3 scripts/ci/check_test_discovery.py

echo ""
echo "=== check_control_exceptions.py (控制路径禁静默 except:pass) ==="
python3 scripts/ci/check_control_exceptions.py

echo ""
echo "=== check_registry_evidence.py (台账9-Gate证据完整性:防照抄/跳门) ==="
python3 scripts/ci/check_registry_evidence.py

echo ""
echo "=== check_holdout_compliance.py (自动选择路径必须 holdout 截断:§5.2 缝③) ==="
python3 scripts/ci/check_holdout_compliance.py

echo ""
echo "=== check_cost_model_pin.py (R-COST-001 CostModel 三费率 hash-pin) ==="
python3 scripts/ci/check_cost_model_pin.py

echo ""
echo "=== check_no_force_promote.py (自动晋级禁 force=True/run_marginal=False:根因#1) ==="
python3 scripts/ci/check_no_force_promote.py

echo ""
echo "=== check_no_legacy_data.py (R-DATA-001 禁代码 import/加载 data_full 旧口径) ==="
python3 scripts/ci/check_no_legacy_data.py

echo ""
echo ""
echo "=== check_amount_units.py (成交额单位 share×raw，禁 volume×100×price) ==="
python3 scripts/ci/check_amount_units.py

echo ""
echo "=== check_fundamental_batch_pit.py (DQ-2026-001 双向 PIT 守卫:R-DATA-003) ==="
# 口径:对 fundamental_batch.parquet 执法 R1 物理(avail<report 零容忍)/R2 真值
# 对账(早知>2d 判未来函数)/R3 晚知形态(断点后>180d)。已登记事故走例外集
# (docs/data_quality/DQ-2026-001_*.csv + ≤2025Q1 窗口豁免),例外只减不增;
# 修复重铺后例外清零退化为纯规则。探针测试 tests/test_fundamental_batch_pit_guard.py。
python3 scripts/ci/check_fundamental_batch_pit.py

echo ""
echo "=== ruff 语义门禁 (F 正确性 + B bugbear + I 排序 + UP 现代化) ==="
# 门禁口径:F/B/I/UP 全仓零违规、零豁免。E 风格项(E501 超长行/E402 scripts sys.path 等)不门禁;
# 存量基线 146 条(F841/B006/B007/B008/B023/B905/UP031/UP042)已于 2026-07-21 分批清零;
# 结构性豁免见 pyproject per-file-ignores(归档已 exclude)。
if ! python3 -m ruff --version >/dev/null 2>&1; then
  echo "❌ 当前 python3 无 ruff 模块,请用项目解释器运行或安装 ruff(≥0.15)"
  exit 1
fi
python3 -m ruff check --select F,B,I,UP .

echo ""
echo "=== mypy 类型门禁 (台账 strategy_registry + 调度 workflow/ + 策略 strategies/) ==="
# 门禁口径:--disallow-untyped-defs --ignore-missing-imports --follow-imports=skip。
# 2026-07-21 分层清零后挂上:台账/调度层基线 24 错(e2363847)、strategies 注解
# 18%→100%(94fb7a95)、跨层暴露的分派冲突 21 错(d429d7d4);lake/ 注解仅 34%,
# 补齐后再纳入口径。mypy 经 uv tool run 执行并 pin 版本,保证各 agent 环境可重复。
if ! command -v uv >/dev/null 2>&1; then
  echo "❌ 无 uv 可执行文件,无法跑 mypy 门禁(uv tool run);请安装 uv 后重跑"
  exit 1
fi
uv tool run mypy==2.3.0 --disallow-untyped-defs --ignore-missing-imports --follow-imports=skip strategy_registry.py workflow/ strategies/

echo ""
echo "=== check_print_budget.py (库层 print 预算 ratchet:get_logger 唯一观测口) ==="
# 口径:默认拒止——factor_research/ 下所有 .py 裸 print=0,除 scripts/tests/scratch/
# apps/data_lake 豁免与白名单冻结项(CLI 表示层/研究报告打印,只降不升)。
# P1-3 已把 workflow/factory/services 156 处库路径 print 归并 get_logger(5c560873);
# 本守卫防新增 print 静默回退,新增正当 CLI print 须在守卫 WHITELIST 冻结并说明理由。
python3 scripts/ci/check_print_budget.py

echo ""
echo "=== test_loop_foundations.py (防自欺地基:trial账本 + holdout金库) ==="
python3 tests/test_loop_foundations.py

echo ""
echo "=== check_lake_writers.py (数据湖唯一写入口) ==="
python3 scripts/ci/check_lake_writers.py

echo ""
echo "=== check_factor_registry.py (因子词表:手工接线冻结/口径与证据/撞名/死模块处置) ==="
python3 scripts/ci/check_factor_registry.py

echo ""
echo "=== test_factor_registry_guard.py (词表守卫 + 注册门 对抗回归) ==="
python3 tests/test_factor_registry_guard.py

echo ""
echo "=== test_engine.py ==="
python3 test_engine.py

echo ""
echo "=== test_data_layer.py ==="
python3 tests/test_data_layer.py

echo ""
echo "=== test_e2e.py ==="
python3 tests/test_e2e.py

echo ""
echo "=== test_knowledge.py ==="
python3 tests/test_knowledge.py

echo ""
echo "=== test_research_run_ledger.py (研究结果统一归档 + index) ==="
python3 tests/test_research_run_ledger.py

echo ""
echo "=== test_autoresearch_engine.py (Auto Factor Research Lite) ==="
python3 tests/test_autoresearch_engine.py

echo ""
echo "=== test_agent_loop.py (5-Component Agent Control Loop) ==="
python3 -m pytest tests/test_agent_loop.py -q

echo ""
echo "=== test_services_phase0.py (产品 services 接缝;全量比对设 PHASE0_FULL=1) ==="
python3 tests/test_services_phase0.py

echo ""
echo "=== test_api_contracts.py (前后端契约 smoke) ==="
python3 tests/test_api_contracts.py

echo ""
echo "=== test_risk_phase3.py (风控控制回路;集成设 PHASE3_FULL=1) ==="
python3 tests/test_risk_phase3.py

echo ""
echo "=== test_agent_phase5.py (Agent planner + 不越权分级) ==="
python3 tests/test_agent_phase5.py

echo ""
echo "=== test_agent_skills.py (Agent skill LLM 意图路由 + 数字护栏) ==="
python3 tests/test_agent_skills.py

echo ""
echo "=== test_stock_profile.py (个股画像 · 不复权真实股价) ==="
python3 tests/test_stock_profile.py

echo ""
echo "=== test_fundamentals.py (基本面引擎:议价权/预期差 + 读层防未来对齐) ==="
python3 tests/test_fundamentals.py

echo ""
echo "=== test_llm_providers.py (Agent LLM 多 provider + 安全不变量) ==="
python3 tests/test_llm_providers.py

echo ""
echo "=== test_settings_phase6.py (系统设置 · 成本铁律只读 + 审计) ==="
python3 tests/test_settings_phase6.py

echo ""
echo "=== test_action_jobs_phase7.py (动作确认令牌 + 分钟级任务异步 job) ==="
python3 tests/test_action_jobs_phase7.py

echo ""
echo "=== test_paper_etf.py (模拟盘债券 ETF 轮动 P5) ==="
python3 tests/test_paper_etf.py

echo ""
echo "=== test_alpha_audit.py (research_toolkit Alpha Audit) ==="
python3 tests/test_alpha_audit.py

echo ""
echo "=== test_factor_store.py (Factor Store 面板落库 + manifest) ==="
python3 tests/test_factor_store.py

echo ""
echo "=== test_factor_store_scoring.py (Factor Store 统一评分层) ==="
python3 -m pytest tests/test_factor_store_scoring.py -q

echo ""
echo "=== test_factor_store_backfill.py (核心因子真实回填编排) ==="
python3 -m pytest tests/test_factor_store_backfill.py -q

echo ""
echo "=== test_regime_gate.py (regime 门控 LIVE 模式,默认关) ==="
python3 tests/test_regime_gate.py

echo ""
echo "=== test_composer.py (capped 权重组合) ==="
python3 tests/test_composer.py

echo ""
echo "=== test_veto_filter.py (Policy 层 VetoFilter 边际贡献机制) ==="
python3 tests/test_veto_filter.py

echo ""
echo "=== test_research_toolkit.py (策略研究与控制规则验证工具箱) ==="
python3 tests/test_research_toolkit.py

echo ""
echo "=== test_engine_start_window.py (BacktestConfig.start 统计窗口语义) ==="
python3 tests/test_engine_start_window.py

echo ""
echo "=== test_lake_invariants.py (数据湖写路径不变量) ==="
python3 tests/test_lake_invariants.py

echo ""
echo "=== test_price_unit_contract.py (价量 canonical 单位) ==="
python3 -m pytest tests/test_price_unit_contract.py -q

echo ""
echo "=== test_price_amount_invariant.py (成交额物理量纲写入闸门) ==="
python3 -m pytest tests/test_price_amount_invariant.py -q

echo ""
echo "=== test_star_exclude.py (科创板 volume 修正 + 小盘显式排除) ==="
python3 tests/test_star_exclude.py

echo ""
echo "=== test_data_vintage.py (每日更新指纹 + 漂移检测) ==="
python3 tests/test_data_vintage.py

echo ""
echo "=== test_style_neutralization.py (CNE6 风格中性化审计) ==="
python3 tests/test_style_neutralization.py

echo ""
echo "=== test_institutional_upgrades.py (机构级治理/组合/容量模块 smoke) ==="
python3 tests/test_institutional_upgrades.py

echo ""
echo "=== test_governance_integrity.py (hit唯一权威/双轨准入/审批映射/ledger链/Nine-Gate摘要) ==="
python3 tests/test_governance_integrity.py

echo ""
echo "=== test_promote_nine_gate.py (promote后自动触发Nine-Gate并失败回填) ==="
python3 tests/test_promote_nine_gate.py

echo ""
echo "=== test_tushare_daily_basic.py (daily_basic 归一 + pivot) ==="
python3 tests/test_tushare_daily_basic.py

echo ""
echo "=== test_pledge_stat_loader.py (质押统计专用防未来对齐) ==="
python3 tests/test_pledge_stat_loader.py

echo ""
echo "=== test_pledge_factors.py (质押风险状态因子) ==="
python3 tests/test_pledge_factors.py

echo ""
echo "=== test_fina_indicator.py (财务指标公告日 ffill 防未来) ==="
python3 tests/test_fina_indicator.py

echo ""
echo "=== test_macro.py (宏观时序层防未来 lag) ==="
python3 tests/test_macro.py

echo ""
echo "=== test_report_nlp_pipeline.py (研报 NLP Inbox 提取管线) ==="
python3 tests/test_report_nlp_pipeline.py

echo ""
echo "=== test_report_feedback_loop.py (研报 自我反馈闭环) ==="
python3 tests/test_report_feedback_loop.py

echo ""
echo "=== test_ontology_shadow_pipeline.py (影子观察本体策略) ==="
python3 tests/test_ontology_shadow_pipeline.py

echo ""
echo "=== test_notify.py (运维告警通道 + 日更失败告警去重/恢复) ==="
python3 tests/test_notify.py

echo ""
echo "=== test_catalog_status.py (边际贡献定级 ACTIVE/SHADOW 台账写入口) ==="
python3 -m pytest tests/test_catalog_status.py -q

echo ""
echo "=== test_moving_average_overlay.py (防御择时参数独立DSR审计单元测试) ==="
python3 -m pytest tests/test_moving_average_overlay.py -q

echo ""
echo "=== test_decision_inbox.py (决策收件箱/今日简报:空箱三态 fail-closed + 透传禁更绿) ==="
python3 tests/test_decision_inbox.py

echo ""
echo "=== 兜底:未被上方显式枚举的 test_*.py 统一 pytest 执行(防新测试静默漏跑) ==="
# check_test_discovery.py 只保证"可被收集",不保证"被执行";本块把没进上方清单的
# pytest 风格测试全部真跑一遍。枚举清单从本脚本自身机械提取(不维护第二份手工清单);
# 已枚举文件(含直跑脚本)不重复执行,其结果只由上方对应行计账。
enumerated=$(grep -E '^python3' scripts/test_all.sh | grep -oE '[[:space:]](tests/)?test_[A-Za-z0-9_]+\.py' | tr -d ' \t' | sort -u)
fallback=()
for f in test_*.py tests/test_*.py; do
  [ -e "$f" ] || continue
  grep -qE '^[[:space:]]*def test_' "$f" || continue  # 纯脚本式测试(无 pytest 函数)豁免,同 check_test_discovery 口径
  grep -qxF "$f" <<< "$enumerated" && continue
  fallback+=("$f")
done
if [ ${#fallback[@]} -gt 0 ]; then
  echo "(兜底执行 ${#fallback[@]} 个未枚举测试文件)"
  python3 -m pytest "${fallback[@]}" -q
else
  echo "(无未枚举测试文件,跳过)"
fi

echo ""
echo "🎉 All tests passed!"
