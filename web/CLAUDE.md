# web/CLAUDE.md — Web 前端开发宪法

> Web 子项目(`quant-research-web`,Next.js + FastAPI)的开发纪律入口。
> 引擎宪法见根 [`../CLAUDE.md`](../CLAUDE.md)(冲突以其为准);产品 UI/UX 规格见 [`../WEB_DESIGN.md`](../WEB_DESIGN.md);落地路线见 [`../Implement.md`](../Implement.md);每日运行命令见 [`../RUNBOOK.md`](../RUNBOOK.md)。
> **接手前端任务先读本文件 + WEB_DESIGN.md。**

---

## 1. 命令(均在 `web/` 下)

```bash
npm run dev          # 启动开发服务(默认 :3000);开发期间禁跑 build(见 §2)
npx tsc --noEmit     # 类型检查(dev 期首选验证)
npm run lint         # ESLint(next lint)
npm run test         # node --test lib/*.test.mjs components/**/*.test.mjs(非 vitest)
npm run build        # 仅在 dev 已关闭时跑;CI/部署用
```

后端(在 `factor_research/` 下,需 `--reload`):`python3 -m uvicorn api.main:app --port 8011 --reload`。前端默认 :3000,后端默认 :8011。

## 2. 前端纪律(P1,违反 = 全站崩溃/口径污染)

1. **禁止 dev 运行时跑 `npm run build`** —— `next dev` 与 `next build` 共用 `.next`,并行会污染 Webpack 缓存导致全站 404/500。`build` 不是 dev 期的常规验证步骤;dev 期验证只用 `npx tsc --noEmit` + `npm run lint`。
2. **缓存损坏自救**(出现 ENOENT / Cannot find module / chunk 404):
   - 强制关闭 dev 任务;
   - `lsof -ti :3000 | xargs kill -9` 释放端口;
   - `rm -rf web/.next web/node_modules/.cache` 清双重缓存;
   - 重启 `npm run dev`,浏览器硬刷新 ⌘⇧R。
3. **端口占用**:`lsof -ti :8011 | xargs kill -9`(前端同理 :3000)。
4. **死文件复活**:Google Drive 同步会复活已删文件 → `rm` 掉;建议把 repo 移出 Drive 同步。

## 3. 作用域规则(P1)

1. **非 Web 任务不得顺手改 `web/`**;Web 任务不得改研究层代码。
2. **Web 展示层不得改变研究口径**:回测口径、成本口径、入册规则、信号生成,一律由引擎层决定,前端只读取/呈现,不得在 UI 层"修正"。
3. Agent 右栏:调仓/下单**只提案不执行**(承根 CLAUDE.md `R-LLM-001` 与不越权门)。
4. 类型纪律:核心对象显式 `interface/type`;避免 `any`(必须用要注释原因)。
5. mock data 集中管理,不在组件内硬编码;AI 生成内容保留免责声明。

## 4. 技术栈

Next.js 14 / React / TypeScript / Tailwind;表格 TanStack Table;请求 TanStack Query;测试 `node --test`(`.test.mjs`)。已有栈优先,不无理由重构。
