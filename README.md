# 环球复盘站 · Global Review Station

零预算、半自动的**客观股市复盘站**。
Python 脚本抓数据填 JSON，复盘观点人工撰写 MDX，GitHub Actions 多时段定时 `fetch → build → deploy` 实现自动更新。

**线上地址**：https://z13516631589.github.io/global-review-station/
**源码仓库**：https://github.com/Z13516631589/global-review-station

> 立场：只做客观记录与事后检验。全站不输出买卖点位、不臆测涨跌结论、**不构成投资建议**。
> 数据纪律：`src/data/*.json` 只放脚本真实抓取的结果，**没有示例数据**；未抓取到的板块显示空态。

---

## 1. 快速开始

```bash
# 1) 安装前端依赖
npm install

# 2) 安装数据抓取依赖（抓取数据前必须安装）
npm run bootstrap:py          # 等价于 pip install -r scripts/requirements.txt

# 3) 抓取数据（写入 src/data/*.json）
npm run fetch

# 4) 本地预览 http://localhost:4321
npm run dev

# 5) 构建
npm run build                 # 产物在 dist/

# 一步到位：先抓数据再预览 / 再构建
npm run dev:fresh
npm run build:fresh
```

> Windows 下若 `python` 不在 PATH，`npm run fetch` 会自动尝试 `python3` / `py`；
> 也可以直接指定：`FETCH_PY="C:/path/to/python.exe" npm run fetch`。

---

## 1.1 数据什么时候会变（重要）

站点是**纯静态**的：页面上的数字来自 `src/data/*.json`，而 JSON 只在**抓取那一刻**被写入。

| 场景 | 数据会更新吗 |
|---|---|
| 关机 / 关闭终端 | 不会，没有任何后台进程在跑 |
| 重新打开项目、`npm run dev` | **不会**——只是把上次抓到的快照重新渲染一遍 |
| `npm run fetch`（或 `dev:fresh` / `build:fresh`） | 会，手动抓一次最新数据 |
| 推到 GitHub + 配好部署 | **会**，由 Actions 在云端定时跑，电脑关机也照跑 |

页面上的「抓取于 xxxx（N 小时前）」由浏览器端实时计算，用来防止把旧快照当新数据看。

---

## 2. 目录结构

```
/
├── src/
│   ├── content/reviews/        # 复盘 MDX（每交易日一篇，YYYY-MM-DD.mdx）
│   ├── data/                   # 脚本生成的 JSON（含 news.json 带分析）
│   │   ├── global_snapshot.json
│   │   ├── ashare.json
│   │   ├── dragons.json
│   │   └── news.json
│   ├── components/
│   │   ├── GlobalSnapshot.astro   # 全球快照
│   │   ├── MarketTable.astro      # A股盘面（指数/涨跌家数/量能/融资）
│   │   ├── SectorBoard.astro      # 主线板块（领涨/领跌 + 驱动）
│   │   ├── DragonList.astro       # 龙虎榜 + 主力资金 TOP
│   │   ├── NewsList.astro         # 消息面（带分析，可筛选）
│   │   ├── KeyWatch.astro         # 标的观察（客观，无彩蛋）
│   │   ├── Scorecard.astro        # 战绩统计
│   │   └── SentimentBar.astro     # 情绪分
│   ├── layouts/Base.astro
│   ├── lib/                       # data.ts（JSON 类型层）/ format.ts（格式化）
│   └── pages/
│       ├── index.astro            # 最新复盘 + 全球快照 + 情绪条
│       ├── reviews/index.astro    # 归档（按月分组）
│       ├── reviews/[slug].astro   # 单篇（11 模块模板）
│       ├── global.astro           # 全球市场
│       ├── news.astro             # 消息面聚合
│       ├── picks.astro            # 标的观察·战绩
│       └── about.astro            # 方法论 / 数据源 / 时间表
├── scripts/
│   ├── common.py                  # 原子写 JSON、日志、北京时间、可选依赖探测
│   ├── fetch_global.py            # 全球快照（yfinance → Stooq 回退）
│   ├── fetch_ashare.py            # A股盘面 / 板块 / 龙虎榜 / 资金流
│   ├── fetch_news.py              # 消息面抓取 + 自动打标签 + 影响分析
│   ├── fetch_all.py               # 一键刷新全部
│   ├── run-fetch.mjs              # npm run fetch 的跨平台包装器
│   └── requirements.txt
├── .github/workflows/update.yml   # 定时自动更新
├── .env.example                   # LLM / 抓取参数（全部可选）
├── astro.config.mjs
├── tailwind.config.mjs            # 注意：package.json 为 ESM，故用 .mjs
└── package.json
```

---

## 3. 半自动工作流

1. **抓数据**：`npm run fetch -- --batch "A股收盘"`（或交给 Actions 定时跑）
2. **写复盘**：新建 `src/content/reviews/YYYY-MM-DD.mdx`，填 frontmatter + 正文
3. **预览**：`npm run dev`
4. **构建部署**：`npm run build` → Vercel / Cloudflare Pages

frontmatter 字段（见 `src/content.config.ts`；下面是**格式示例**，数字请替换为当日真实数据）：

```yaml
---
title: "9月21日复盘：……"   # 标题写当日真实走势
date: 2026-09-21
author: 复盘小组
batch: A股收盘            # A股收盘 / 美股收盘 / 盘前消息
dataUpdatedAt: "2026-09-21 15:05"
sample: true              # true 时页面打「示例复盘」标记
summary: "一句话客观定调"
tags: ["缩量", "轮动加快"]
plan:                     # 明日预案：客观条件分支
  - condition: "指数放量收复今日高点且成交环比转正"
    action: "维持现有仓位，按原观察条件跟踪，不追加"
picks:                    # 标的观察（客观记录，非建议）
  - code: "002371"
    name: "北方华创"
    buy: "回踩 445 - 455 区间"
    stop: "跌破 425"
    position: "不超过总仓位 8%"
    logic: "连续三日涨幅偏离触发上榜"
    status: "观察中"
record:                   # 历史战绩
  total: 8
  win: 4
  loss: 3
  avgReturn: 0.86
---
```

---

## 4. 消息面自动分析

`scripts/fetch_news.py`：

```
抓取（akshare 财联社/全球快讯/财经早餐 + 可选 RSS）
  → 标题指纹去重
  → LLM 分析（兼容 OpenAI 的免费额度接口：Gemini / DeepSeek / 通义）
  → 失败自动回退规则词典（关键词 → 板块映射 + 加权情感 + 模板句）
  → 输出 { title, source, time, sentiment, sector, analysis, url }
```

**Prompt 硬约束**：只做「事件 → 影响板块 / 方向」的事实性归纳，不输出买卖点位、不臆测涨跌结论。

配置（`.env` 或 CI Secrets，全部可选）：

| 变量 | 说明 |
|---|---|
| `LLM_API_BASE` | OpenAI 兼容端点，如 `https://generativelanguage.googleapis.com/v1beta/openai` |
| `LLM_API_KEY` | API Key |
| `LLM_MODEL` | 默认 `gemini-2.0-flash` |
| `DISABLE_LLM` | 设为 `1` 强制走规则兜底 |
| `NEWS_MAX_ITEMS` / `NEWS_LLM_MAX_CALLS` | 条数上限 / 单次 LLM 调用上限 |
| `NEWS_RSS` | 自定义 RSS，逗号分隔 |

不配置任何变量也完全可用——自动回退规则分析，站点永不缺分析、永不报错。

---

## 5. 自动更新时间表（cron 为 UTC）

| 批次 | 北京时间 | UTC cron | 星期 | 抓取重点 |
|---|---|---|---|---|
| A股收盘 | 15:30 | `30 7 * * 1-5` | 周一~周五 | 盘面 / 指数 / 板块 / 消息面 |
| A股盘后 | 18:30 | `30 10 * * 1-5` | 周一~周五 | 龙虎榜 / 融资余额 / 主力资金（均为盘后披露） |
| 美股收盘 | 约 04:30 / 05:30 | `30 20 * * 2-6` | 周二~周六 | 全球快照（美股 / 中概 / 港股） |
| 盘前消息（可选） | 09:00 | `0 1 * * 1-5` | 周一~周五 | 隔夜外盘 + 晨间要闻 |

每个触发点执行：`fetch 全部数据 → astro build → deploy`。

为什么要拆成「收盘 + 盘后」两批：龙虎榜、融资余额、主力资金流都是**收盘后**才披露的，
15:30 那一批只能拿到上一交易日的这些字段；18:30 补跑一次才能拿到当日口径。

**GitHub Actions 调度的时间误差**：cron 只是“触发请求”，实际排队时间通常在 5~30 分钟，
高峰期可能更久甚至被跳过；仓库连续 60 天无活动后定时会被自动停用。对时效敏感的话，
把对应批次提前 10~15 分钟即可。

每个触发点执行：`fetch 全部数据 → astro build → deploy`。
美股交易日（ET 周一~周五）对应北京时间周二~周六凌晨，故星期字段为 `2-6`；`20:30 UTC` 是夏/冬令时的折中取值。

**部署配置**：默认 Vercel（`amondnet/vercel-action`），需配置 `VERCEL_TOKEN` / `VERCEL_ORG_ID` / `VERCEL_PROJECT_ID`。
未配置时工作流会退化为上传 `dist` 产物（可配合 GitHub Pages 使用）。换 Cloudflare Pages 只需替换部署步骤为 `cloudflare/pages-action`。

---

## 6. 健壮性设计

- **原子写入**：JSON 先写临时文件再 `os.replace`，构建中不会读到半截文件。
- **字段级兜底**：`merge_with_previous()` 在单个接口失败时用上一版对应字段填充，避免整页空白。
- **接口名回退**：akshare 函数名经常变动，每个数据需求都列了多个候选函数名依次尝试。
- **数据源回退**：
  - A股指数：新浪财经 → 东方财富；
  - 行业板块：新浪行业板块 → 东财行业/概念板块；
  - 全球快照：新浪（美股指数日K / 港股指数）→ 东财 → yfinance → Stooq；
  - 新闻：akshare → RSS；分析：LLM → 规则词典。
- **重试**：`retry_call()` 对代理/数据源偶发抖动自动重试。
- **失败不影响构建**：`run-fetch.mjs` 在数据抓取失败时仍返回 0，`npm run build` 永远能过。
- **空态优先，不造假数据**：仓库不内置任何虚构行情。未抓取数据时页面显示空态 + 抓取指引；
  所有页面展示的数字均来自脚本实际抓到的公开数据，并在页脚标注更新时间与批次。

---

## 7. 免责声明

本站所有行情与新闻均为**延迟数据**，来自公开免费数据源，可能存在延迟、缺失或口径差异；
消息面影响分析由规则或模型自动生成，仅作事实性归纳。
**本站内容不构成任何投资建议**，据此操作风险自负。
