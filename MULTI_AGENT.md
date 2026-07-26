# 多 Agent 系统级分工(作战地图)

> 三个 agent 平台共读本文件——各自打开 repo 即知在系统里的位置、该干什么、交接格式。
> 配套:`CLAUDE.md`(操作宪法 + LLM 分工铁律)、`SPEC.md`(架构)、`STATUS.md`(进度)。

## 组织原则:按「工作负载时间形态 × agent 可用性模型」分工,不按「谁聪明/谁闲」

| 资源 | 可用性模型 | 适合的负载 |
|---|---|---|
| **DeepSeek**(API) | 充值即持续,7×24 永不停 | **常驻、长跑、无人值守**的高频苦力 |
| **Codex CLI**(订阅) | 额度/时间有限,爆发式 | **可并行的编码冲刺**(独立 worktree) |
| **Antigravity**(订阅,Gemini 3) | 额度/时间有限,爆发式 | **浏览器自主控制** + 长上下文文档批量 |
| **Claude Code**(Opus 4.8) | 主编排 | 架构、判断代码、契约、协调 |

**核心铁律(违反 = 系统会因订阅到期而停摆):**
1. **常驻系统只依赖 DeepSeek + 确定性代码,禁止依赖任何订阅 agent 在线**。
2. **订阅 agent 在 build-time / acquisition-time(爆发产出产物),不在 run-time**。它们产出代码/下载的文件,交给常驻系统 7×24 消费;产出后即走,别空转烧额度。
3. **判断恒为确定性代码**(Alpha Audit NW+RidgeCV+置换 / L0-L3 / 回测 / 入册门槛)——任何 agent、任何模型都不得代替(承 `CLAUDE.md` LLM 分工铁律)。
4. **worktree 隔离 + 文件不重叠**:两个编码 agent 绝不同时改同一批文件(必冲突)。跨轨交接走明确目录/格式契约。
5. **提交纪律(所有 agent 一律遵守:Claude / Codex / Antigravity / 任何后来者)**:共享工作树下,**绝不 `git add -A` / `git add .` / `git commit -a`**——一锅端会把别的 agent 半成品改动卷进你的 commit。只用**显式路径** `git add <file>...`,提交前 `git diff --cached` 核对每行都 trace 到本次意图,别人的改动留在工作树不碰;不擅自切分支 / reset / rebase 共享分支。完整 6 点见 `CLAUDE.md` 工作约定「提交纪律」;codex / Antigravity / Cursor 另见仓库根 `AGENTS.md`(跨工具共读)。

---

## 三层系统

### ① 常驻骨干(DeepSeek API,7×24,按 $ 扩)
系统的"心跳",只认 DeepSeek(`services/agent/llm_adapter.py::get_adapter()`):
- 因子候选生成 loop(`run_autoresearch_llm` → 候选 → L0-L3 判决)
- 研报 / 公告 / 新闻**批量 NLP**(数据到了就处理)
- 日常 LLM 解读(日信号解读、异常解释)
- → 跑成 cron/daemon,funded by API。**绝不在此层依赖订阅 agent。**

### ② 爆发专家(订阅,额度有限 → 只花在各自的「边」)
- **Antigravity**(浏览器 + 长上下文):研报 PDF 抓取 run、web 数据源勘探、大文档批量分析。**用完即停**。
- **Codex CLI**(并行编码):有可并行的实现块时,独立 worktree 冲刺,写完提交即走。

### ③ 架构 / 编排(Claude Code)
设计 ① 的常驻管线、写判断代码、定 ② 的交接契约、协调三轨。

---

## 交接契约(谁产出什么给谁消费)

```
Antigravity ──抓研报PDF──▶ data_lake/research_pdf/<date>/*.pdf ──▶ DeepSeek常驻NLP(7×24)──▶ 结构化信号 ──▶ 因子
Codex ──写独立模块──▶ git worktree → commit ──▶ 常驻管线在DeepSeek上跑(run-time不需codex)
Claude Code ──设计管线+判断代码+契约+协调──▶ 全程
```

- **研报-NLP 解耦**:Antigravity 抓取(订阅 burst)→ 落 `data_lake/research_pdf/`;DeepSeek 处理(API 常驻)。抓取与处理解耦,处理永不因订阅到期而停。
- **PDF 解析**:`opendataloader_pdf.convert(format='markdown')`(需 Java;直接调引擎,绕开有 bug 的 LangChain 封装)。
- **数据落库**:统一走 `lake/`、`scripts/data/`(数据湖唯一写入口守卫);新数据集进 `TUSHARE_DATASETS` 注册表 + `load_tushare_panel` 加载。

---

## 当前三轨切分(文件不重叠)

| 轨 | Agent | 任务 | 不碰 |
|---|---|---|---|
| 取数 | **Antigravity** | 浏览器去巨潮/东财批量下研报 PDF → `data_lake/research_pdf/` | 任何现有代码 |
| 编码 | **Codex** | 宏观时序层(cn_cpi/ppi/m2/shibor/moneyflow_hsgt,市场级 1 行/天,新 macro storage/loader) | 股票面板 registry / `lake/load_lake.py` 股票部分 |
| 编码+常驻 | **Claude Code** | tushare 股票维度收尾(cyq_perf/limit_list_d 重跑)+ 研报-NLP 处理端(PDF→DeepSeek→信号) | `data_lake/research_pdf/` 由 Antigravity 写 |

---

## 给每个 agent 的一句话

- **DeepSeek**:你是常驻苦力——生成候选、批量抽取,但你的产出**必过代码闸**才算数,你不做判断。
- **Codex**:你在独立 worktree 写不相交的模块,写完提交即走;别碰别人正在改的文件。
- **Antigravity**:你的边是浏览器和长文——抓数据、勘探源、读大文档;抓完落到约定目录就停,别空转。
- **Claude Code**:你定架构、写判断代码、守契约、协调三轨;判断永远在代码里,不外包。
