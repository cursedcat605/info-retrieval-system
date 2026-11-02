# 部署到 GitHub Pages（静态化方案）

本文说明如何把「选调生去向信息检索系统」发布成**纯静态站点**，挂到 GitHub Pages 上，
用于课堂演示等「多人同时访问、但没有云服务器」的场景。

> ⚠️ 关键前提：站点里是**未脱敏的真实姓名**（门户公开公示数据）。
> GitHub Pages 的免费额度只对 **public 仓库**开放，因此本方案等于
> 「把这份数据公开到互联网上」。确认可接受后再继续，
> 详见仓库 `.gitignore` 里的提醒。

---

## 一、方案原理

原来是 FastAPI + SQLite 的后端在算检索，Pages 只能托管静态文件，所以：

1. **导出阶段**（本地跑一次）：`scripts/build_static.py` 连本地 SQLite，把 455 条记录、
   455 份详情、分面统计、搜索建议全部预渲染成一份 `api/bootstrap.json`；
   原图同时压成 WebP（宽 ≤1000、质量 82），10.0 MB / 92 张。
2. **运行时**（浏览器里）：`static-api.js` 拦截页面发出的所有 `/api/*` 请求，
   在浏览器端完成「筛选 / 排序 / 分页 / 高亮 / FTS5 相关度」，返回与后端**同构**的 JSON。
   - 检索逻辑（含 `bm25` 精确打分与 `_RELEVANCE_FTS` 的并列取舍）是逐行移植的，
     已用 63 条查询 + 10 个接口与 FastAPI 后端做过逐字段对拍，结果完全一致。
   - 前端 62 个文件原样复用，只把 `/styles.css`、`/api` 这类**根路径**改写成相对路径，
     这样站点挂在 `https://<user>.github.io/<repo>/` 子目录下也不会 404。
3. **依赖**：ECharts 已内置到 `vendor/echarts.min.js`，字体的 Google Fonts 只出现在
   不发布的 `preview.html`。**导出的站点不请求任何外部域名**，教室网络不通外网也能用。

产物目录 `docs/` 直接入库，GitHub Pages 直接发布它。

---

## 二、一次性配置

### 1. 把仓库转为 public

```powershell
gh repo edit cursedcat605/info-retrieval-system --visibility public --accept-visibility-change-consequences
```

也可以网页操作：仓库 → **Settings** → **General** → 最下方 **Danger Zone** →
**Change repository visibility** → **Public**。

### 2. 先把 `docs/` 推到 main

Pages 只是「把仓库里某个目录当网站发出去」，目录不存在就只会一直 404。
所以要**先**推送，再开 Pages：

```powershell
git add docs scripts/build_static.py web/static DEPLOY-GITHUB-PAGES.md
git commit -m "feat: 静态化导出，可发布到 GitHub Pages"
git push
```

确认远程真的有这个目录：

```powershell
gh api repos/cursedcat605/info-retrieval-system/contents/docs --jq '.[].name'
```

### 3. 开启 Pages

**推荐网页操作**（最省事，也不会踩下面那些命令行坑）：
仓库 → **Settings** → **Pages**

- **Source**：`Deploy from a branch`
- **Branch**：`main`，目录选 **`/docs`**
- 保存，等 1~2 分钟构建

或用命令行（**必须是一整行**，见下方「为什么粘贴后没反应」）：

```powershell
gh api -X POST repos/cursedcat605/info-retrieval-system/pages -f "source[branch]=main" -f "source[path]=/docs" --jq '.html_url, .status'
```

查询当前状态：

```powershell
gh api repos/cursedcat605/info-retrieval-system/pages --jq '.status, .html_url, .source'
```

常见返回值：`built` 表示已发布；`null` 或 404 表示还没开。

> ⚠️ 如果 `docs/` 还没推上去就先开了 Pages，构建会立刻失败，日志里是这样一句：
> `Error: No such file or directory @ dir_chdir0 - /github/workspace/docs`
>
> 这**不是配置错了**，只是没东西可发。把 `docs/` 推上去后，Pages 会在下次 push 时自动重建。
> 想手动触发一次：仓库 → **Actions** → `pages build and deployment` → **Re-run all jobs**。

#### 为什么粘贴后没反应

那条命令原本被我写成了**带反引号换行**的两行：

```text
gh api -X POST .../pages `
  -f 'source[branch]=main' -f 'source[path]=/docs'
```

反引号续行只在 **PowerShell** 里有效，且要求反引号后**绝对不能有空格**。三种典型翻车：

| 你粘到哪 | 现象 |
| --- | --- |
| PowerShell，行尾反引号后被塞了空格 | 第一行当场执行 → 报 422/404 |
| PowerShell，只粘了第一行 | 提示符变成 `>>`，光标干等 → **看起来没反应**（此时按 `Ctrl+C` 退出再重来） |
| bash / Git Bash / WSL / VS Code 的 bash 终端 | 反引号是**命令替换**开始符，shell 一直等配对的反引号 → **卡死不动** |
| cmd.exe | 反引号不是续行符（cmd 用 `^`）→ 报语法错误 |

所以统一改成**一整行 + 双引号**，这样 PowerShell / bash / cmd 都能直接跑。

### 4. 访问

```
https://cursedcat605.github.io/info-retrieval-system/
```

首次生效约 1~2 分钟；之后每次 push 到 `main` 的 `docs/` 变更也会自动重新发布。

---

## 三、日常更新流程

数据库变了（重新 OCR、补录手工表）之后：

```powershell
# 1. 重新导出站点（会自动复用未变化的 WebP，只压新增图）
python scripts/build_static.py

# 2. 本地看一眼
python -m http.server 8000 --directory docs
#   打开 http://127.0.0.1:8000/ 验证检索、详情、统计三页

# 3. 提交并推送
git add docs
git commit -m "data: 重新导出静态站点"
git push
```

`docs/` **必须入库**，不要加进 `.gitignore`。

---

## 四、容量与并发

| 项目 | 实测 | Pages 免费额度 |
| --- | --- | --- |
| 站点体积 | **11.6 MB**（106 个文件） | 仓库建议 ≤1 GB |
| 单次首屏 | `bootstrap.json` 494 KB（gzip 后约 120 KB） | 100 GB/月 软带宽 |
| 原图 | 10.0 MB / 92 张 WebP，按需加载 | — |

- 100~200 人同时打开，主要压力在 `bootstrap.json` 的下载，
  GitHub 的 CDN 完全吃得下（之前自测本机单进程 uvicorn 约 130 rps 才是瓶颈，
  静态化后瓶颈转移到 CDN，等于绕开了这个问题）。
- 只有点进详情页才会拉那张原图，平均不到 110 KB/次。
- 想更省流量：把 `scripts/build_static.py` 里的 `MEDIA_MAX_WIDTH` / `MEDIA_QUALITY` 调小后重建。

---

## 五、注意事项

- **演示前先预热**：教室网络第一次访问要下 494 KB JSON，建议课前先用手机流量打开一次，
  让 CDN 与浏览器缓存就绪。
- **不要用 `file://` 直接开 `docs/index.html`**：`fetch` 会因跨源策略失败。
  必须走 HTTP（`python -m http.server`，或已发布的 Pages 地址）。
- `.nojekyll` 已生成，避免 GitHub 的 Jekyll 处理 `_` 开头路径。
- 站点内**没有**后端能力：`/api/health` 返回的 `db_path` 是说明文字，
  `config.static` 为 `true`；`fuzzy.path` 等本机路径字段在导出时已被剥离。
- 如果以后要接自定义域名，在 **Settings → Pages → Custom domain** 填，并加 `docs/CNAME`。

---

## 六、可选：用 Actions 自动重建

若希望「push 代码后自动重新导出」，把 `data/db/selects.sqlite` 一并入库（当前已入库），
再添加 `.github/workflows/pages.yml`：检出 → `pip install -r requirements.txt` →
`python scripts/build_static.py` → `actions/upload-pages-artifact`（path: `docs`）→
`actions/deploy-pages`。此时 Pages 的 Source 要改成 **GitHub Actions**。
注意 workflow 需要 `pillow` 才能压图。

---

## 七、和另外两种方案的对比

| 方案 | 是否需服务器 | 100~200 人并发 | 是否需要脱敏 | 备注 |
| --- | --- | --- | --- | --- |
| **A. 静态化 + Pages（本文）** | 否 | ✅ CDN 承载 | 否（但需公开仓库） | 数据只读，检索在浏览器 |
| B. Codespaces / 本地 + 隧道 | 否（借本机） | ⚠️ 受家庭上行带宽限制 | 可选 | 隧道地址每次重启会变 |
| C. Hugging Face Spaces | 否 | ✅ | 可选 | 需 Docker，冷启动慢 |
