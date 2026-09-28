# 选调生去向信息检索系统

> 面向高校选调生**经验分享 / 去向信息**的采集、识别、整理与检索一体化系统。
>
> 数据链路：**校内信息门户爬取图片 → OCR 文字识别 → 文本清洗与结构化 → 数据库入库 → 全文检索 / Web 展示**

---

## 一、项目简介

高校选调生的经验分享帖子、公示名单、去向统计等资料，往往以**图片形式**发布在学校信息门户、就业信息网或内部通知中，难以直接检索。本项目通过自动化流水线解决这一问题：

1. **数据采集**：登录（或半自动）访问学校信息门户，抓取与"选调经验分享"相关的图片及页面元数据。
2. **OCR 识别**：对抓取到的图片批量进行文字识别（中文为主，兼顾表格/排版）。
3. **文本清洗与结构化**：纠错、去噪、分词，抽取关键字段（年份、省份、选调单位、学历、专业等）。
4. **数据入库**：整理为标准结构化数据，写入关系型数据库 / 全文索引。
5. **信息检索**：提供关键词、条件筛选、模糊匹配等检索能力，并可选择性地提供 Web 界面。

---

## 二、数据流水线

```mermaid
flowchart LR
    A[学校信息门户<br/>信息发布页面] -->|爬虫抓取| B[原始图片 + 页面元数据]
    B --> C[OCR 文字识别]
    C --> D[文本清洗 / 纠错 / 分词]
    D --> E[字段结构化<br/>年份·省份·单位·学历…]
    E --> F[(数据库 / 全文索引)]
    F --> G[检索服务]
    G --> H[Web 前端展示]
```

**流水线各阶段及其对应目录：**

| 阶段 | 说明 | 主要目录 |
| --- | --- | --- |
| ① 采集 | 从信息门户抓取相关图片与元数据 | `src/crawler/` → `data/raw/` |
| ② 识别 | 图片 OCR 转文字 | `src/ocr/` → `data/interim/ocr_text/` |
| ③ 清洗 | 文本纠错、去噪、分词 | `src/preprocess/`、`src/nlp/` |
| ④ 入库 | 结构化并写入数据库 | `src/database/` → `data/db/`、`data/processed/` |
| ⑤ 检索 | 关键词 / 条件检索服务 | `src/search/`、`web/backend/` |
| ⑥ 展示 | 前端检索界面 | `web/frontend/` |

---

## 三、目录结构总览

```
信息检索系统/
├── README.md                  # 项目说明（本文件）
├── requirements.txt           # Python 依赖清单
├── .env.example               # 环境变量模板（账号、路径、密钥）
├── .gitignore                 # Git 忽略规则
│
├── config/                    # 配置文件
├── data/                      # 数据目录（分阶段存放）
│   ├── raw/                   # 原始数据（爬虫产物，只增不改）
│   │   ├── images/            #   原始图片
│   │   └── metadata/          #   页面元数据（URL、标题、发布时间等）
│   ├── interim/               # 中间数据
│   │   └── ocr_text/          #   OCR 识别出的原始文本
│   ├── processed/             # 已清洗、结构化的数据（CSV/JSON）
│   └── db/                    # 数据库文件（SQLite 等）
│
├── src/                       # 源代码（核心业务逻辑）
│   ├── crawler/               #   数据采集：门户登录、页面解析、图片下载
│   ├── ocr/                   #   OCR 识别模块
│   ├── preprocess/            #   文本清洗、纠错、标准化
│   ├── nlp/                   #   中文分词、关键词/实体抽取
│   ├── database/              #   数据库连接、建表、读写（ORM / SQL）
│   ├── search/                #   检索逻辑（查询解析、排序、过滤）
│   └── utils/                 #   通用工具（日志、配置加载、文件 IO）
│
├── scripts/                   # 一键运行脚本（串联流水线各阶段）
├── web/                       # Web 应用
│   ├── backend/               #   检索 API 服务（FastAPI/Flask）
│   └── frontend/              #   检索界面（HTML/Vue/React）
│
├── models/                    # 本地 OCR / NLP 模型文件（如 PaddleOCR 权重）
├── tests/                     # 单元测试与集成测试
├── docs/                      # 项目文档（需求、设计、数据字典、使用手册）
├── logs/                      # 运行日志（爬取、OCR、入库过程记录）
├── notebooks/                 # 探索性数据分析（Jupyter Notebook）
└── docker/                    # 容器化部署配置（Dockerfile / compose）
```

---

## 四、各文件夹功能详解

### 1. `config/` — 配置中心
集中存放所有可调参数，避免硬编码：
- `config.yaml`：爬虫目标 URL、限速、OCR 语言与置信度阈值、数据库连接等全局配置。
- 门户相关的栏目 ID、登录方式、Cookie 模板等敏感/易变配置放这里。
- 实际密钥通过 `.env` 注入，`.env.example` 提供模板。

### 2. `data/` — 数据分层存储
按"原始 → 中间 → 成品"三层组织，便于回溯与重跑：

| 子目录 | 存放内容 | 特点 |
| --- | --- | --- |
| `data/raw/images/` | 爬取到的原始图片 | **只增不改**，文件名带日期/哈希防冲突 |
| `data/raw/metadata/` | 图片对应的页面元数据（标题、来源 URL、发布时间、标题关键词） | JSON/CSV，用于溯源 |
| `data/interim/ocr_text/` | OCR 输出的**原始**文本（未清洗） | 保留识别错误，便于对比优化 |
| `data/processed/` | 清洗、结构化后的数据（字段化 CSV/JSON） | 入库前的最终产物 |
| `data/db/` | SQLite/数据库文件、全文索引文件 | 可被检索服务直接打开 |

### 3. `src/crawler/` — 数据采集
- **登录/会话管理**：处理信息门户的登录、验证码、Cookie 保持。
- **页面解析**：定位"选调经验分享"栏目，提取文章列表与图片链接。
- **图片下载**：支持重试、限速、去重（按 URL/图片哈希），写入 `data/raw/images/`。
- **元数据记录**：将标题、URL、发布时间等写入 `data/raw/metadata/`。

### 4. `src/ocr/` — 图片文字识别
- 对 `data/raw/images/` 批量识别，支持**中文 + 英文/数字**混排。
- 可接入 PaddleOCR / Tesseract / 云 OCR，模型权重放 `models/`。
- 输出原始识别文本到 `data/interim/ocr_text/`，并记录置信度、框选坐标。
- 针对经验分享常见的**长图、截图、聊天记录截图**做版面切分与结果拼接。

### 5. `src/preprocess/` — 文本清洗与纠错
- 去除 OCR 噪声（乱码、多余空格、页眉页脚水印）。
- 常见 OCR 错字纠正（借助词典或大模型校正）。
- 文本标准化：全半角、繁简、统一标点。

### 6. `src/nlp/` — 中文自然语言处理
- 关键信息抽取：**姓名、届别、所属学院、专业、去向单位、城市、岗位**（核心 7 字段），另附省份、学历、届别年份。
- `gazetteer.py`：省份/城市/学院/机构后缀/专业等白名单词典，决定“哪些字符串**有可能**是目标字段”。
- `field_extractor.py`：按“主讲人区块”切分文本并抽取字段，规则只做**裁剪**不做**补全**，每个字段都记录了原文依据。
- `pinyin.py`：姓名的全拼 / 声母缩写生成与匹配（`张三` → `zhangsan` / `zs`），支撑拼音搜索。
- `position_category.py`：由岗位/单位/专业派生“岗位类别”（公务/事业单位、技术研发类、教育类、金融类、其他）与“学历层次”（本科/硕士/博士）。
- 停用词、同义词、自定义词典（`config/dict/selects_terms.txt`，选调相关术语）。

### 6.1 核心字段数据字典

| 字段 | 含义 | 示例 | 写入列 |
| --- | --- | --- | --- |
| `name` | 选调生姓名 | 张三 | `selects_records.name` |
| `cohort` | 届别（保留“届/级”原意） | 2024届 / 2021级 | `selects_records.cohort` |
| `college` | 所属学院 | 民族学与社会学学院 | `selects_records.college` |
| `major` | 专业 | 文物与博物馆 | `selects_records.major` |
| `destination_org` | 选调去的单位 | 湖南省株洲市炎陵县鹿原镇三口村 | `selects_records.destination_org` |
| `city` | 城市 | 株洲市 | `selects_records.city` |
| `position` | 岗位 | 党总支书记助理 | `selects_records.position` |

> ⚠️ **隐私提示**：需求要求保留**姓名**，故数据库中存储了真实姓名。
> 对外提供 API / 展示时建议在接口层做脱敏（如 `余**`），参考 `src/search/` 的展示层。

### 7. `src/database/` — 数据入库
- `schema.py`：SQLite 表结构 DDL —— `images`（图片/通知元数据）、`selects_records`（7 个核心字段）、`selects_fts`（FTS5 全文索引，trigram 分词）。
- `repo.py`：连接、UPSERT、全量重建 FTS、检索、分面统计（供前端筛选面板）。
- 提供增删改查接口，将 `data/processed/` 结构化数据写入 `data/db/selects.sqlite`。

### 8. `src/search/` — 检索逻辑
- `engine.py`：查询解析与组装。多关键词默认 AND，命中为空时退回 OR 并按命中数排序；筛选、排序、分页、分面、标签均在此汇总。
- `highlight.py`：把命中词（含拼音映射回汉字）在姓名/学院/专业/城市/省份/单位/岗位/文章标题等字段上包 `<mark>`，并在原文中截取摘要；**先转义再高亮**，防止 XSS。
- 多条件组合筛选：**同维度内 OR、不同维度间 AND**，计数基于排除自身维度的分面。
- 相关度排序（FTS5 `bm25` + 置信度）、结果分页。
- 基于 SQLite FTS5（trigram 分词）；数据量上量后可平滑换 Elasticsearch。

### 9. `src/utils/` — 通用工具
- 日志配置（同时输出到 `logs/`）、配置加载、路径管理、文件读写、异常处理等公共代码。

### 10. `scripts/` — 流水线脚本
串联各阶段，支持一键/定时执行：
- `crawl_notices.py`：爬取信息门户「通知公告」中的经验分享图片（已实现）。
- `run_ocr.py`：批量 OCR → `data/interim/ocr_text/*.txt|*.json`（已实现）。
- `extract_records.py`：OCR 结果清洗 + 结构化 → `data/processed/*.jsonl|csv`（已实现）。
- `derive_fields.py`：派生姓名拼音 / 岗位类别 / 学历层次（支持 `--force` 重算）。
- `build_database.py`：写入 SQLite + 重建 FTS5 索引（已实现）。
- `check_search.py` / `check_api.py` / `smoke_live.py`：检索层与接口自检（见第八章）。
- `run_all.py`：端到端执行全流程（待实现）。

### 11. `web/` — Web 应用
- `web/backend/`：FastAPI 检索 API（搜索 / 详情 / 分面 / 统计 / 补全 / 健康检查），并托管前端静态文件与原图。
- `web/frontend/`：检索界面（`index.html`）、详情页（`detail.html`）、统计页（`stats.html`），原生 HTML + 原生 JS + 手写 CSS，**无需构建步骤**。

### 12. `models/` — 模型文件
存放本地 OCR / NLP 模型权重（如 PaddleOCR、分词自定义模型）。体积较大，通常加入 `.gitignore`。

### 13. `tests/` — 测试
`pytest` 用例：拼音匹配、岗位分类规则回归、高亮/XSS、检索引擎（筛选·排序·分页·分面·详情）。
数据库以**只读**方式打开（`file:...?mode=ro`），保证测试不会改动生产数据。运行：`python -m pytest tests -q`。

### 14. `docs/` — 项目文档
需求说明、系统设计、数据库数据字典、爬虫与使用手册。

### 15. `logs/` — 日志
保存爬取、OCR、入库等运行日志，便于排查与审计。

### 16. `notebooks/` — 探索分析
Jupyter Notebook，用于数据分布探索、OCR 效果评估、检索效果调优等。

### 17. `docker/` — 容器化部署
`Dockerfile` 与 `docker-compose.yml`，统一环境、便于部署。

---

## 五、技术栈

| 环节 | 选型 | 状态 |
| --- | --- | --- |
| 语言 | Python 3.10+ | ✅ 实际使用 3.13 |
| 爬虫 | requests / httpx、BeautifulSoup / lxml、Selenium / Playwright（动态页面） | ✅ httpx + lxml |
| OCR | RapidOCR（onnxruntime，**默认**，pip 直装离线可用）、PaddleOCR（备选）、Tesseract（备选） | ✅ RapidOCR |
| 文本处理 | 正则 + 白名单词典、jieba、可选大模型纠错 | ✅ 正则 + 词典 + jieba |
| 拼音检索 | pypinyin（全拼 + 声母缩写） | ✅ |
| 数据库 | SQLite（默认，含 FTS5）/ MySQL / PostgreSQL | ✅ SQLite + FTS5(trigram) |
| 全文检索 | SQLite FTS5 / Elasticsearch | ✅ FTS5 |
| Web 服务 | FastAPI（后端）、Vue / React / **原生 HTML**（前端） | ✅ FastAPI + 原生 HTML/JS |
| 图表 | ECharts（CDN），断网时自动降级为纯 CSS 条形图 | ✅ |
| 测试 | pytest + FastAPI TestClient | ✅ 109 项 |
| 定时任务 | APScheduler / 系统计划任务 | ⬜ 待实现 |

---

## 六、快速开始

```bash
# 1. 创建并激活虚拟环境
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux / macOS

# 2. 安装依赖
pip install -r requirements.txt

# 3. 复制并填写配置
copy .env.example .env        # Windows
# cp .env.example .env        # Linux / macOS
#   在 .env 中填写门户账号、数据库路径等

# 4. 按流水线依次运行
python scripts/crawl_notices.py     # ① 采集图片 → data/raw/
python scripts/run_ocr.py           # ② OCR 识别 → data/interim/ocr_text/
python scripts/extract_records.py   # ③ 清洗 + 字段抽取 → data/processed/
python scripts/derive_fields.py     # ④ 派生姓名拼音 / 岗位类别 / 学历层次
python scripts/build_database.py --reset   # ⑤ 入库 + 建 FTS5 索引 → data/db/selects.sqlite

# 5. 启动 Web 服务（检索界面 + API）
python -m web.backend.main          # → http://127.0.0.1:8000

# 6. 自检（可选）
python scripts/check_search.py      # 检索层 39 项断言
python scripts/check_api.py         # HTTP 接口 61 项断言
python -m pytest tests -q           # 单元 / 集成测试 109 项
```

> 端口、监听地址、允许的 CORS 来源都在 `config/config.yaml` 的 `web:` 段落中配置。

---

## 七、爬虫使用说明（信息门户经验分享图片）

数据来源：信息门户「通知公告」模块（`my.muc.edu.cn/page/11#/notice/noticeList`）中
筛选"选调分享"得到经验分享通知，正文图片位于 `/comsys-portal-notice-web/upload/ueditor/`。

**鉴权特点**：列表接口 `getNoticeByPage` 需要登录会话；而**图片本身公网可直接下载**。
因此采用「浏览器带会话取数据 + 本地服务落盘下载」的模式，无需在代码里保存账号密码。

### 目录与产物

| 路径 | 说明 |
| --- | --- |
| `src/crawler/notice_crawler.py` | 爬虫核心：解析接口 JSON、提取图片、下载、写元数据 |
| `src/crawler/collect_service.py` | 本地采集服务（接收浏览器导出的接口 JSON） |
| `scripts/crawl_notices.py` | 命令行入口 |
| `data/raw/images/` | 下载的图片，命名 `日期_通知ID_序号_图注.ext` |
| `data/raw/metadata/notice_api_*.json` | 接口原始返回，便于溯源/重跑 |
| `data/raw/metadata/notice_images_*.csv/json` | 图片元数据（含 sha256、来源 URL、通知标题等） |

### 方式一：采集服务模式（推荐，无需密码入库）

```bash
# 1) 启动本地采集服务
python scripts/crawl_notices.py --serve --port 8765

# 2) 在已登录的信息门户页面（浏览器控制台）执行：
#    调用接口并把数据发给本地服务，服务端自动下载图片
fetch('/comsys-portal-notice-web/getNoticeByPage', {
  method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
  body: new URLSearchParams({
    currentPage: '1', pageSize: '100', searchValue: '选调分享', state: '', is_add: '0',
    totalCounts: '0', total: '-1', select_all: 'false', select_notice: '', type: '',
    searchDepartment: '', start_date: '', end_date: '', system_show: '1'
  }).toString(),
  credentials: 'include'
})
  .then(r => r.json())
  .then(j => fetch('http://127.0.0.1:8765/collect?keyword=选调分享', {
    method: 'POST', body: JSON.stringify(j)
  }))
  .then(r => r.json()).then(console.log);
```

### 方式二：离线模式（处理已保存的接口 JSON）

```bash
python scripts/crawl_notices.py --from-json data/raw/metadata/notice_api_选调分享.json --keyword 选调分享
```

### 方式三：Cookie 自动化模式

自行从浏览器导出 Cookie 到 `data/raw/cookies.json`（`[{ "name": "...", "value": "..." }]`），然后：

```bash
python scripts/crawl_notices.py --keyword 选调经验 --cookie-file data/raw/cookies.json --page-size 50
```

> ⚠️ `data/raw/cookies.json` 含登录态，已在 `.gitignore` 中忽略，切勿提交或分享。

### 已抓取结果（截至 2026-09-28）

- 「选调分享」：3 条通知 / 3 张图片
- 「选调经验分享」：89 条通知 / 92 张图片
- 合计 **92 张图片，63.8 MB**，全部校验为有效图片，0 失败

---

## 八、检索与 Web 使用说明

### 8.1 启动

```bash
python -m web.backend.main        # 监听 127.0.0.1:8000
```

| 页面 | 地址 | 说明 |
| --- | --- | --- |
| 检索首页 | `/` | 搜索框 + 分面筛选 + 卡片/表格切换 |
| 信息详情 | `/detail.html?id=<记录ID>` | 基本信息 / 选调生信息 / 来源信息 三栏 |
| 统计分析 | `/stats.html` | 省份、届别、岗位类别、学院等图表 |
| 接口文档 | `/docs` | FastAPI 自动生成的 Swagger UI |
| 图片原文 | `/images/<文件名>` | 原图直出，详情页可对照 |

#### 8.1.1 常驻运行与开机自启（推荐）

前台 `python -m web.backend.main` 会随终端一起关闭，这正是「网站突然打不开」的常见原因。改用守护脚本：

```powershell
# 后台启动（无窗口、单实例、崩溃自动重启）
wscript scripts\serve-hidden.vbs

# 查看状态（含最近日志）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\serve.ps1 -Status

# 停止（写入停止标记，登录时不再自动拉起）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\serve.ps1 -Stop
```

| 项 | 说明 |
| --- | --- |
| 开机自启 | 「启动」文件夹已放置快捷方式 `选调生检索系统 Web 服务.lnk`，删除即取消自启 |
| 单实例 | 启动前探测 `config.yaml` 的 `web.port`，已在监听则不重复启动 |
| 自动重启 | 服务进程意外退出后 5 秒自动拉起，不限次数 |
| 守护日志 | `logs/web-server.log`（启停事件）/ `.out.log` / `.err.log`，每次重启轮转为 `.prev` |
| 恢复自启 | 删除 `logs/web-server.stop`，再运行 vbs 或双击启动项 |

> 读到 `退出码 -1（进程被强制结束）` 属于 `Stop-Process`／任务管理器强杀的返回值，**不是**服务自身崩溃。

### 8.2 检索能力对应关系

| 需求 | 实现位置 | 说明 |
| --- | --- | --- |
| 拼音搜索 `zhangsan` → `张三` | `src/nlp/pinyin.py`、`src/search/highlight.py` | 全拼与声母缩写均可；命中后**反向映射回汉字**再高亮 |
| 多条件筛选（同维度 OR、跨维度 AND） | `src/database/repo.py::_build_from_where` | 维度：省份 / 岗位类别 / 学历 / 届别 / 学院 / 专业 / 城市 |
| 筛选标签与分面计数 | `src/database/repo.py::facet_counts` | 计数**排除自身维度**，避免选中后归零 |
| 排序 | `repo.SORT_ORDERS` | 相关度 / 届别新→旧（默认） / 届别旧→新 / 姓名拼音 / 置信度 |
| 分页 | `src/search/engine.py` | 默认 20 条/页，上限 100，UI 为 5 页窗口 + 跳页 |
| 结果高亮 | `src/search/highlight.py` | 先转义再包 `<mark>`，覆盖 10 个字段 + 原文摘要 |
| 卡片 / 表格视图 | `web/frontend/app.js` | 一键切换，偏好写入 `localStorage` |
| 统计分析 | `web/frontend/stats.js` + `/api/stats` | ECharts；CDN 不可用时自动降级为纯 CSS 图表 |

### 8.3 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 健康检查（记录数、图片数） |
| `GET` | `/api/config` | 前端元数据：7 个筛选维度、排序选项、分页默认值 |
| `GET` | `/api/search` | 关键词 + 多条件筛选 + 排序 + 分页，返回结果与分面 |
| `GET` | `/api/record/{id}` | 单条详情（含结构化字段、原文、字段证据） |
| `GET` | `/api/facets` | 各维度可选项与计数 |
| `GET` | `/api/stats` | 聚合统计（省份 / 届别 / 岗位类别 / 学院 / 学历 …） |
| `GET` | `/api/suggest` | 搜索框自动补全候选 |

示例：

```bash
# 拼音检索：zhangsan → 张三
curl "http://127.0.0.1:8000/api/search?q=zhangsan"

# 跨维度 AND：重庆市 ∧ 硕士
curl "http://127.0.0.1:8000/api/search?province=重庆市&degree_level=硕士"

# 姓名拼音排序，第 2 页
curl "http://127.0.0.1:8000/api/search?sort=name_pinyin&page=2"
```

### 8.4 自检脚本

| 脚本 | 作用 | 结果 |
| --- | --- | --- |
| `scripts/check_search.py` | 不启服务，直接校验检索层：查询解析、分面、排序、分页、高亮 | 39 项全过 |
| `scripts/check_api.py` | 用 `TestClient` 跑完整 HTTP 链路（含静态资源与 404） | 61 项全过 |
| `scripts/smoke_live.py` | 对**已启动的真实服务**发请求（需先 `wscript scripts\serve-hidden.vbs` 或 `python -m web.backend.main`） | 6 项全过 |
| `pytest tests -q` | 拼音、岗位分类、高亮、检索引擎的单元/集成测试 | 109 项全过 |

> `smoke_live.py` 的拼音用例默认用占位名 `zhangsan`（仓库内不出现真实姓名）。
> 若要校验真实数据，先在当前会话设 `$env:SMOKE_PINYIN_QUERY="<姓名全拼>"` 再运行。

---

## 九、注意事项

- **合规性**：仅采集有权限访问的校内公开信息，遵守学校信息门户的使用条款与相关法律法规，控制访问频率，不得用于传播个人隐私。
- **脱敏**：涉及姓名、学号、联系方式等个人信息时，应在入库前进行脱敏处理。
- **可复现**：`data/raw/` 为只增不改的原始层，任何清洗逻辑变更都能基于原始数据重跑。
- **日志留痕**：所有爬取与识别过程记录到 `logs/`，便于审计与问题定位。

---

## 十、后续可扩展方向

- 增量更新机制：定时爬取新发布内容并增量入库。
- 接入大模型进行智能问答（"某省 2024 年一般都去哪些单位？"）。
- 人名消歧与同名合并；跨届别去向对比分析。
- `position_category` / `degree_level` 目前仅作为筛选与统计列，未入 FTS 索引（它们是**派生**列，无原文可高亮）；若需直接搜索“金融类”可再评估入索引。
- 详情页的“查看原文”直链（`config/config.yaml` 中的 `web.portal_detail_url`）待补：需确认门户通知详情的稳定 URL 规则。
