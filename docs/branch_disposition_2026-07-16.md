# 未合并分支处置清单（2026-07-16）

## 目的

这份清单回答三个问题：分支是否仍有独立价值、应如何进入 `main`、何时可以删除。
判定以补丁内容和验证结果为准，不以“分支未合并”或提交数量代替审查。

基线：`origin/main` 为 `af829f3f`。本轮集成分支为
`codex/branch-convergence-20260716`，只提取能够独立解释、独立验证和独立回滚的改动。

## 处置结果

| 分支 | 状态 | 处置 | 理由 / 删除条件 |
| --- | --- | --- | --- |
| `codex/converge-governance` | 已完整提取 | 集成后删除 | 3 个治理提交已逐项提取：readiness 失败关闭、holdout vault 失败关闭、部署目录与研究目录分离。进入 `main` 且 `git cherry` 全部显示等价后可删。 |
| `ws2-composite` | 已完整提取 | 集成后删除 | 组合搜索及周维护调度已提取，保留了主线已有的 portfolio recompose 作业。进入 `main` 且补丁等价后可删。 |
| `claude/thirsty-rhodes-40ed0c` | 核心价值已提取 | 归档后删除 | `n_trials` 低报守卫已提取；剩余状态文档已被主线后续状态取代。进入 `main`、确认无独立代码补丁后可删。 |
| `codex/xiaochengxu` | 部分提取 | 保留，禁止整分支合并 | 已提取桌面端源码、AutoResearch 缓存、金额单位、质押状态等独立功能；仍含全球数据源、原型、生成报告和混合提交，必须分簇审查。 |
| `claude/brave-golick-426851` | 内容过时 | 归档，不合并 | 仅含 ADR-030；相关治理结论已被主线后续 ADR 和实现覆盖。归档前核对是否仍有未迁移的决策背景。 |
| `claude/daily-round-6-review` | 运行结果过时 | 重跑，不合并旧产物 | 仅含 2026-07-13 的 metasearch 重跑产物。研究结果应从当前主线和当前数据重跑，不把旧生成文件当代码合并。 |
| `claude/epic-dubinsky-3a3f52` | 独立实验 | 保留 | AlphaAgentEvo / AlphaMemo 等 opt-in 实验有独立产品与成本边界，应单独评审，不混入本轮治理收敛。 |
| `claude/gracious-joliot-801c36` | 高价值待审 | 保留，优先单独集成 | 含 registry、退市历史、portfolio gate 等 27 个治理提交，影响面大，需要单独对抗审查和全量回归。 |
| `claude/naughty-raman-133948` | 独立子系统 | 保留 | 35 个 Loop OS 提交构成完整子系统，需要产品边界和架构评审，不能作为零散修复合并。 |
| `claude/sleepy-euclid-e50e34` | 归档方案待决 | 保留 | 8 个提交已与主线补丁等价，剩余 9 个涉及执行、模型风险和模块裁剪；删除行为需要负责人确认。 |
| `ws0-ws4-topn` | 架构改造待审 | 保留 | 2 个提交已补丁等价；其余包含 44 文件架构改造及 R-PROD 文档，应单独验证，不能靠提交标题判定已覆盖。 |

## 本轮已提取的独立意图

1. 治理失败关闭：生产 readiness、holdout vault、部署与研究目录边界。
2. 桌面端真实源码：Electron/Vite 客户端、只读 agent CLI、对话连续性和技能路由。
3. 研究正确性：AutoResearch 内容寻址缓存、Alpha101 去退化、Amihud 金额口径。
4. 数据与因子：`pledge_stat` 标准加载器、质押风险状态信号。
5. 全仓金额单位：统一为 `volume(shares) * raw_price`，并增加静态守卫。
6. 证据治理：阻止组合策略把 `n_trials` 低报为 1。
7. 组合搜索：保留周维护已有任务并增加 composite search。

## 明确不自动合并的内容

- `codex/xiaochengxu` 中的全球数据源适配器：授权、PIT、覆盖率、失败关闭和数据湖写入边界尚未完成 S0-S7 准入审查。
- 原型视频、截图、临时 scratch、生成报告和历史运行输出：属于可再生或大体量产物，不作为产品代码进入主线。
- 小程序原型：与当前桌面产品边界不同，需要独立产品决策。
- `fb24c83c`：单提交混合 53 个文件和约 1.2 万行，包含代码、报告、manifest 与状态改写，不具备独立回滚性，禁止整提交提取。

## 验证与清理规则

- 已完成针对性测试：治理 34 项、桌面 19 项、agent 28 项、研究正确性 12 项、质押 14 项、金额 16 项、registry evidence 12 项、组合搜索 7 项。
- 已通过相关静态守卫：holdout compliance、control exceptions、layer deps、amount units、registry evidence。
- 全量 `factor_research/scripts/test_all.sh` 已于 2026-07-16 完整通过：全部守卫通过，
  146 个 `test_*.py` 均被发现，兜底执行的 96 个未显式枚举文件通过（1 项按设计跳过）。
- 只有当改动已进入 `main`、`git cherry main <branch>` 不再显示独立补丁、关联工作树无用户文件且无运行进程时，才允许删除分支和工作树。
- 清理不得使用 `git reset --hard`、强制删除含未知文件的工作树，或以 `git add -A` 打包未归属改动。
