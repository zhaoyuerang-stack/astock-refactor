# RUNBOOK.md —— 每日运行手册(一页)

> 目标:每天按这页跑通「数据 → 信号 → 自动模拟盘 → 监控 → 解读」。系统会自动执行 paper_trade 模拟盘,但**不自动真实下单**,实盘执行决策留给人。
> 命令均在 `factor_research/` 下;Web 在 `web/` 下。

---

## ① 每天早上(5 分钟)

```bash
cd /Users/kiki/astcok/factor_research

# 推荐:带新鲜度校验的生产入口(数据未达预期最新交易日则跳过出信号,不会用旧数据硬出)
python3 scripts/ops/scheduled_daily_update.py

# 手动入口(出当日信号:联网增量更新 + 质量校验 + 择时 + 持仓 + 调仓判断 → signals/YYYY-MM-DD.json)
# 注意:数据更新失败时会继续用旧数据出信号,务必看 [2] 质量校验输出确认未 stale
python3 run_daily.py                 # 不想联网就加 --no-update,用现有数据
```
看输出 6 步:`[2]` 质量校验、`[3]` regime(🟢BULL/🔴BEAR)、`[5]` 调仓判断。
- **BEAR → 空仓**,闲置资金建议配 `511010 国债ETF`;**BULL → 按 Band exposure(0~1.5x) 配置 illiq top-25**。
- 距上次调仓 **≥20 交易日** 才换仓,否则继续持有/观望。

产物:`signals/今天.json`(timing/regime/holdings/action)、`signals/state.json`(当前仓位)。

---

## ② 打开产品看板(Web)

```bash
# 终端A(后端,必须 --reload,改代码自动加载)
cd /Users/kiki/astcok/factor_research && python3 -m uvicorn api.main:app --port 8011 --reload
# 终端B(前端,dev 模式热重载)
cd /Users/kiki/astcok/web && npm run dev
```
浏览器开 **http://localhost:3000**,后端默认 **http://127.0.0.1:8011**,九页看这些:
| 页面 | 每天看什么 |
|---|---|
| 总览 | 当前状态(空仓/持仓)、因子健康、数据质量预警 |
| 数据中心 | 质量判定应=「可用」;severe(负价/OHLC)应=0 |
| 风险控制 | verdict(正常/预警/超限)+ 待确认 ControlAction |
| 组合管理 | 当前 vs 目标组合(目标=选股层 top-25) |
| 因子研究 / 研究实验 | 因子家族 / 假设池漏斗 + 已登记实验 |
| 右栏 Agent | 问「当前风控如何」「数据质量怎样」→ DeepSeek V4 真解读;**调仓/下单只提案不执行** |

---

## ③ 红灯排查(看到就停)

| 现象 | 含义 / 处置 |
|---|---|
| `validate_final.py` 退出码 1 | severe 数据真问题(负价/OHLC)→ 查 `lake/quarantine.json` 隔离,或 `repair_ohlc` |
| 数据中心 verdict ≠ 可用 | 严重数据问题,**不建议回测**,先修数据 |
| 风控 verdict = 超限 | 看 ControlAction,人工二次确认后才动(系统不自动执行) |
| 因子健康 trend「减速」+ 夏普下滑 | 母策略可能衰减,进 LESSONS / 考虑退役 |

---

## ④ 每周 / 按需

```bash
python3 validate_final.py                      # 全市场数据质量(severe>0 即非零退出)
python3 strategy_lake.py                        # 真实口径复测(2018-2026 + 2010 压力)
python3 strategy_registry.py                    # 母策略台账对比
python3 scripts/ops/generate_factor_health.py   # 刷新 reports/factor_health.json
python3 scripts/ops/paper_trade.py              # 纸面账户跟踪 → paper/
python3 scripts/research/cost_sensitivity.py     # 成本敏感性
bash scripts/test_all.sh                         # 全套测试 + 分层守卫(改完代码必跑)
python3 scripts/ops/git_hygiene_audit.py         # 只读审计分支/worktree/脏文件
```
定时任务(launchd):`scripts/ops/install_launchd_jobs.sh`(每日更新 / 每周维护 / FastAPI :8011 / Web :3000)。

### 仓库卫生(每周或合并前)

先刷新远端引用,再运行只读审计:

```bash
cd /Users/kiki/astcok
git fetch origin --prune
python3 factor_research/scripts/ops/git_hygiene_audit.py
```

报告中的处置口径:

- `unmerged_branches`:逐条进入合并、提取、归档、删除四选一;不能按名称或日期直接删。
- `clean_merged_worktrees`:仅列 tracked/untracked/ignored 均为空的候选,仍需确认没有运行中的 agent。
- `merged_branches_without_worktree`:可删除的本地引用候选;远端分支另行审批。
- `untracked_buckets`:按 `review_source/review_evidence/archive_or_promote/local_ignore/review`
  分桶;工具不会自动 stage、移动或删除任何文件。

需要机器可读结果时加 `--json`;工具默认不联网,只有显式 `--fetch` 才刷新 `origin`。

---

## ④.5 paper 多账户上线验收(WS-D 执行侧,人工验收清单)

> 背景:`.claude/plans/PLAN_paper_multiaccount_loop.md` T1-T5 已在 worktree 完成代码侧
> (`portfolio/paper_accounts.py` + `scripts/ops/paper_accounts_update.py` + 读层/API +
> web 展示),全部测试 hermetic(合成数据),**未在生产机跑过真实数据**。上线前必须由人
> 在生产机(`/Users/kiki/astcok/factor_research`,非本 worktree)按下列步骤验收,worktree
> 无数据湖/无真实 paper 状态,不能替代这一步。

**前置条件**:代码已从本 worktree 合并到生产分支(合并本身不在本计划范围,由人决定何时合并)。

1. **确认候选名单来源健康**
   ```bash
   cat reports/research/portfolio_recompose.json | python3 -m json.tool | head -20
   ```
   检查 `generated_at` 是否 ≤14 天前、`paper_candidates` 是否非空。若为空或过期,先跑
   `python3 scripts/ops/scheduled_portfolio_recompose.py` 补一次周度组合再构成
   (advisory,不改任何台账)。

2. **首次 provision(不改动任何现有单账户 paper 状态)**
   ```bash
   python3 -m scripts.ops.paper_accounts_update --dry-run
   ```
   核对输出:每个候选版本的 provision 状态(active/blocked/unknown)与原因是否符合预期。
   **确认 `paper/account.json`(legacy 单账户,illiquidity 生产 paper 状态)文件字节未变**
   (`git diff` 或 `md5` 对比 provision 前后)——dry-run 只读 registry/recompose,不该碰
   `paper/accounts/` 以外的任何文件。

3. **正式 provision + 首次更新**
   ```bash
   python3 -m scripts.ops.paper_accounts_update
   ```
   检查 `paper/accounts/summary.json` 与各 `paper/accounts/<family>__<version>/` 目录
   (account.json/trades.csv/nav.csv/meta.json)是否按预期生成。

4. **连续观察 ≥2 个交易日**,每天跑一次(或等 launchd 挂载后自动跑),核对:
   - 每账户 `nav.csv` 是否新增一行(active 账户)/ 保持不变(blocked/frozen 账户)。
   - `trades.csv` 的成交记录是否与该版本 spec 的目标持仓逻辑吻合(可抽查 1-2 只标的手算)。
   - Web `/paper-accounts` 页面(dashboard「排名靠前策略并排实测」区块)展示是否与
     `summary.json` 一致,顺序是否与 `paper_candidates` 排名一致。

5. **核对 legacy 单账户 paper 流零 diff(关键回归检查)**
   ```bash
   python3 -m scripts.ops.paper_trade   # legacy 单账户入口,行为应与上线前逐字段一致
   git diff paper/account.json paper/trades.csv paper/nav.csv   # 若在 git 跟踪范围内;
   # 实际生产上 paper/ 多半 gitignored,改用「上线前后手工备份 + diff」核对：
   # cp paper/account.json /tmp/account_before_multiaccount.json (上线前先备份)
   # 上线后跑几天,再 diff 持仓/现金逻辑是否符合预期演化(不应有除多账户新增文件外的
   # 任何字段级异常跳变)。
   ```

6. **挂载调度(可选,人工决定是否启用)**
   `scripts/ops/scheduled_daily_update.py` 已接好 `run_paper_accounts_update` 旁路
   (紧跟既有 `run_paper_trade` 之后,同一份日更报告 `reports/ops/daily_update/*.json`
   会多一个 `paper_accounts_update` 字段)。确认其失败不影响 `report["status"]`
   (与 `smallcap_forward` 同款旁路纪律)后,该步骤已随 `scheduled_daily_update.py`
   自动生效,无需额外挂 launchd job。

7. **验收通过标准**:连续 2 个交易日 nav.csv/trades.csv 正确增长、legacy 单账户
   paper 流零异常、Web 展示与后端一致 → 视为上线验收通过,可将
   TASKS.md「【WS-D 执行侧】paper 多账户并行实测绑定」标记为完成。
   任一步骤异常 → 停止,回滚(仅需删除 `paper/accounts/` 目录及移除
   `scheduled_daily_update.py` 里的 `run_paper_accounts_update` 调用行,legacy 单账户
   流完全不受影响,因为两者账户目录完全隔离)。

---

## ⑤ LLM(Agent 大脑)配置

设置页「AI 模型配置」填 → 保存 → 测试连接(绿✓即通):
- **DeepSeek**:provider=`openai_compatible`,model=`deepseek-v4-flash`(或 `-pro`),base_url=`https://api.deepseek.com/v1`
- 其余示例见 `app_config/settings.yaml::ai_model` 注释(Qwen/Kimi/GLM/Ollama/OpenAI/Anthropic)
- Key 存 gitignored 文件,不进 git;留空=不改 LLM 永不下单/越权。

---

## ⑥ 常见运行态坑(本项目踩过)

| 坑 | 解 |
|---|---|
| 后端改了路由没生效 / 页面接口 404 | uvicorn 用 `--reload`;或杀掉重启 |
| 前端白屏 / chunk 404 | **别在 dev 跑 `npm run build`**(污染 `.next`);dev 运行时只用 `npx tsc --noEmit` + `npm run lint` 做验证;若已污染,先 `lsof -ti :3000 | xargs kill -9` 停 dev,再 `rm -rf web/.next` 后重启 `npm run dev`,最后浏览器硬刷新 ⌘⇧R |
| 端口被占 | `lsof -ti :8011 \| xargs kill -9`(前端同理 :3000) |
| 删过的死文件又冒出来 | Google Drive 同步会复活已删文件 → `rm` 掉;建议把 repo 移出 Drive 同步 |

---

## 能力边界(诚实)
跑通 = **研究决策闭环**(数据→信号→自动模拟盘→监控→解读),**刻意停在真实账户执行之前**。
未做:真实盘下单(无券商接口)、行业/市值集中度风控(缺 industry_map)、异步 Worker、多用户/Auth。
