# AGENTS.md — 通用 agent 项目指令(A股全市场因子量化研究)

> **定位**:跨工具 agent 的**协作底线**(共享工作树下的提交纪律等),所有 agent 共读。
> **何时读**:任何 agent 接手本仓、尤其动 git 之前。
> **不归这管**:数据/策略/架构**铁律** → [CLAUDE.md](CLAUDE.md);哪个**平台**干哪类负载 → [MULTI_AGENT.md](MULTI_AGENT.md);每步流程谁干 → [WORKFLOW.md](WORKFLOW.md)。

`AGENTS.md` 是**跨工具共读**的项目级 agent 指令:Codex、**Antigravity**、Cursor、Claude Code 等都会读它。
本仓库由**多个 agent 并发共享同一个工作树**,本文件给所有 agent 立共同的协作底线;
完整开发宪法见 [CLAUDE.md](CLAUDE.md)(数据/策略/架构铁律、成本模型、分层依赖、台账唯一写入口),
分工与交接见 [MULTI_AGENT.md](MULTI_AGENT.md)。**冲突时以 CLAUDE.md 为准**(Antigravity 另需让位其 GEMINI.md)。
接手先读 CLAUDE.md + STATUS.md。

## 提交纪律(最高优先级,违反 = 卷走别人改动/无法 revert)

多 agent 共享工作树时,烂提交会把别人的半成品一起焊死进历史。必须:

1. **一个 commit = 一个完整、可独立 revert 的意图**。宁可拆成几个小而自洽的 commit,不要一个"什么都做了"的大杂烩。
2. **绝不 `git add -A` / `git add .` / `git commit -a`**。一锅端会卷进别的 agent 正在改的文件。
   **只用显式路径** `git add <file>...`,只 stage 你 trace 得清、属于本次意图的文件。
3. **提交前必核对范围**:`git diff --cached --stat` + `git diff --cached`,确认每个文件、每一行都 trace 到本次目的;
   别人的改动**留在工作树,不碰**。
4. **不擅自切分支 / reset / rebase / amend 共享分支或别人的 commit**。动 git 历史前先 `git status`
   看有无他人正在改的文件;有疑问先停下问,别先斩后奏。
5. **message 讲"为什么"**:`type(scope): 标题`(conventional commits) + 正文写根因(diff 看不出的部分)
   和验证证据;提交前先跑守卫+测试,绿了再提。
6. **数据湖/大体量运行产物不入库**(`data_lake/`、`scratch/*.csv`、运行结果);这也是不能 `add -A` 的硬理由。

## 分支 / worktree 生命周期

`main` 只做可信基线,不做开发工作台。多 agent 并行时必须遵守:

1. **一个任务 = 一个短期分支 = 一个独立 worktree**。从最新 `origin/main` 创建,任务结束即进入合并、
   提取、归档或删除四选一,不得无限期悬空。
2. **脏 `main` 不继续开发**。先查清已有改动归属;新任务在独立 worktree 开始,不得用 stash/reset
   暂存或抹掉别人的现场。
3. **分支超过 7 天必须复核**。独有提交多、与主线双向分叉的分支不得整体盲合;从最新主线新建分支,
   按单一意图 cherry-pick 或重做仍有价值的部分。
4. **删除前必须机械核对**:分支已被 `origin/main` 包含或独有提交已明确处置、关联 worktree 无
   tracked/untracked/**ignored 本地内容**且不在运行、远端状态已刷新。`git branch --no-merged`
   只证明提交祖先关系;疑似 squash/cherry-pick 时再用 `git cherry origin/main <branch>` 检查补丁等价性。
5. **已合并不等于可立即删**。只要关联 worktree 有 tracked/untracked/ignored 内容,一律保留并标记
   负责人;禁止 `git worktree remove --force` 和 `git branch -D` 代替审查。

固定审计入口(只有审计脚本本身只读):

```bash
git fetch origin --prune
python3 factor_research/scripts/ops/git_hygiene_audit.py
```

审计工具只报告,不删除分支、worktree 或文件。

## 提交 / 归档 / 忽略判定规则

脏工作树必须先分桶,再处理。逐项回答:

1. **是否决定系统行为或研究事实?** 只有源代码、测试、契约、canonical 配置/台账和活文档才可能提交。
2. **是否可重新生成?** 可由安装、构建、回测或渲染恢复的内容默认忽略;提交生成源和必要配置。
3. **是否有长期审计价值?** 只有被 ADR、任务、台账或研究登记簿引用且可复现的证据才进入仓库。
4. **是否含本机或私密状态?** token、个人 workspace、外部绝对路径、IDE/agent 运行状态一律不提交。
5. **能否独立 revert?** 无法和本次意图一起解释的文件留在原处,另立任务,不得顺手带入。

| 类别 | 默认动作 | 边界 |
| --- | --- | --- |
| 生产源码、测试、CI 守卫 | 提交 | 必须有验证证据,不能夹带生成物 |
| canonical 配置/台账 | 谨慎提交 | 只能走规定写入口;说明口径、生成方式和影响 |
| 活文档、ADR、运行手册 | 提交 | 必须是当前真相或已采纳决策;普通草稿/行业材料不因是 Markdown 自动提交 |
| 可复现实验报告 | 有引用才提交 | 必须被 ADR/任务/台账/登记簿引用,并写明数据版本、命令、样本、成本、T+1/`shift(1)` 和失败边界 |
| 探索脚本 | 转正或留本地 | 被复用/引用后迁入 `scripts/research/` 或 `scripts/ops/`,补最小测试或 dry-run;否则留 `scratch/` |
| 共享 agent skill | 谨慎提交 | `.agents/skills/` 中自包含、无私密路径的项目 skill 可提交;指向 `$HOME` 的 symlink/个人安装只进 `.git/info/exclude` |
| 运行产物、依赖、缓存 | 忽略或删除 | `node_modules/`、`dist/`、日志、临时 JSON/CSV、缩略图、渲染和 profiler 输出 |
| 私有/本机配置 | 忽略 | token、`project.private.config.json`、`auth.json`、个人 workspace 和本地 agent 状态 |
| 数据湖/行情缓存 | 忽略 | 数据 payload 永不提交;已跟踪 manifest 的日常刷新也不提交,仅 schema/版本语义变化可带审计理由提交 |

归档只保存**不可由现有源重新生成且仍有决策价值**的内容。历史方案放 `docs/archive/`,文件头必须写明
"历史参考,不作实现依据";纯中间产物不归档。不要整目录忽略 `.agents/`,否则会把未来的共享项目 skill
一并藏掉。

提交前固定检查:

```bash
git status --short --branch
git diff --name-only
git ls-files --others --exclude-standard
git diff --cached --stat
git diff --cached
```

只允许显式路径 stage。出现 `scratch/`、本机配置、缓存、渲染产物或私密配置时默认停止并重新分桶。

## 与 CLAUDE.md 共通的铁律(摘要,详见 CLAUDE.md)

- **分层依赖单向**:`data(lake) → factors → core.engine → {strategies, factory/workflow} → registry → production`;
  提交前跑 `python3 factor_research/scripts/ci/check_layer_deps.py` 确认无倒灌。
- **回测唯一权威 = `core.engine.BacktestEngine`**;台账唯一写入口 = `strategy_registry.register()`;
  数据湖写入口 = `lake/` 或 `scripts/data/`。
- **真实口径不可动**:样本、公式、成本、`shift(1)`、T+1 一律不许为"达标"而改。
- **接入新数据源必须先读固定剧本** [`data_source_onboarding.md`](factor_research/docs/agent_skills/data_source_onboarding.md)
  再动手:S0 立项五判 → 小样本探针 → 契约声明(时间轴口径三选一,防未来函数) → 回填 → 质量门 →
  统一加载层 → 登记,逐步 fail-closed。**不得临场自创接入流程**;历史工程事故(东财封禁/单位错
  100 倍/幸存者偏差/未来泄露)全部源于跳过这些步骤。数据落湖 ≠ 因子有效,价值判断另走 probe。
- **一键检查**:`bash factor_research/scripts/test_all.sh`(分层+数据湖守卫 + 全部测试)。
