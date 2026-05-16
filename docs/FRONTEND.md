# 前端说明（拾影视频下载器 / Video Grab）

技术栈：**Vue 3**、**Vite 7**、**TypeScript**、**lucide-vue-next**；导图使用 **markmap-lib / markmap-view**，XMind 导出使用 **jszip**。

## 目录与入口

| 路径 | 职责 |
|------|------|
| `frontend/src/App.vue` | 单页壳：顶栏主导航、**视频搜索**（`SearchPanel`）与 **视频下载**（链接解析、结果分栏、AI）、能力一览、页脚、设置弹层 |
| `frontend/src/SearchPanel.vue` | 关键词搜索、分页、跨页勾选、NDJSON `/api/search`、`BulkDownloadModal` |
| `frontend/src/BulkDownloadModal.vue` | 批量画质确认弹窗 |
| `frontend/src/TasksPanel.vue` | 批量任务列表与进度、ZIP 下载 |
| `frontend/src/style.css` | 全局样式与响应式布局 |
| `frontend/src/apiConfig.ts` | API 根地址、OpenAI Key、B 站 Cookie 的 `localStorage` 与请求头 |
| `frontend/src/mindmapExport.ts` | 思维导图导出（SVG / PNG / XMind）的纯函数实现 |

## 布局与交互

### 主导航（`mainTab`: `search` \| `download` \| `tasks`）

- **视频搜索**：渲染 `SearchPanel`；勾选多条后 **下载选中** 打开 `BulkDownloadModal` 选画质，确认后 `POST /api/download-batches` 并切换到 **下载任务**。
- **视频下载**：渲染 Hero + 解析/结果区。搜索页 **用链接下载** 会切换到本 Tab 并自动解析。
- **下载任务**：`TasksPanel` 轮询 `GET /api/download-batches/{id}`，展示子项进度与 ZIP 下载链接。
- 点击品牌可切回 **视频搜索**。

### 顶栏

- 左侧品牌；中间 **视频搜索** / **视频下载** / **下载任务**；右侧 **设置**。

### 视频搜索（`SearchPanel.vue`）

- 支持 **YouTube / 哔哩哔哩** 关键词搜索；B 站失败时接口可能返回 `warning`。
- 分页：`page_size` 为 10 / 20 / 30；切换**搜索源**或**每页条数**会清空勾选（全局 `Map` 选中集）。
- 卡片：勾选、封面（经 `image-proxy`）、**用链接下载**、**视频总结**。顶栏：**已选 N 条**、清空、**下载选中**（`BulkDownloadModal` → `POST /api/download-batches`）。

### 视频下载 — 首屏

- **Hero**：主标题「公开视频一键解析下载」+ 两条要点（使用流程与可选 AI）。
- **下载面板**：标题「解析下载」+ 后端就绪状态胶囊；URL 输入与「解析视频」按钮。
- **合规**：`<details>` 默认收起。
- **告警**：后端不可达或依赖缺失时，单行摘要 + 可展开的排查说明（含指向 **设置** 中 API 根地址的提示）。

### 设置

- **连接**：后端 API 地址（可选；留空走同源 `/api`）。
- **AI 视频总结**：OpenAI API Key、Base URL、模型名（保存在 `localStorage`，经请求头发往后端）。
- **哔哩哔哩**：Cookie（可选）。
- **保存** 后重新健康检查；下载页会清空当前解析结果。

详见 [CONFIGURATION.md](./CONFIGURATION.md)。

### 解析成功后（双栏）

- **左栏**：封面与元信息、格式卡片、开始下载；高度作为右侧 AI 栏的参照。
- **右栏**：AI 总结面板；桌面宽度下通过 CSS 变量与 `ResizeObserver` 使右侧滚动区域高度与左栏对齐（小屏单列自然堆叠）。

### AI 总结（Tab）

总结完成后，内容按 Tab 渐进展示：

| Tab | 内容 |
|-----|------|
| 概要 | 字幕来源（若有）、**大纲**、**核心要点** |
| 时间轴 | **分段摘要**（时间段 + 文案） |
| 导图 | **Markmap** SVG；工具栏含 **全屏**、**缩放 +/-**；**SVG / PNG / XMind** 导出 |
| 对话 | 多轮问答 |

导图 Tab 下 SVG 仅在当前 Tab 挂载，离开 Tab 会销毁 Markmap 实例，进入时重新 `fit()`。

### 思维导图导出

- **SVG**：克隆 SVG 并注入 Markmap 样式后下载。
- **PNG**：将矢量结果绘制到 Canvas（可调像素倍率）。
- **XMind**：生成 XMind 8 兼容的 ZIP（`content.xml` + `META-INF/manifest.xml`）。

导出前会临时放大 SVG 视口并 `fit()`，再恢复原视图，避免截取不全。

## 与后端的约定

1. **开发模式**（`npm run dev`）：请求同源 `/api/...`，由 Vite 代理到 `VITE_API_TARGET`（默认 `http://127.0.0.1:8028`）。环境变量见 `frontend/.env.example`。
2. **用户配置了 API 根地址**：所有 JSON API、图片代理与「保存到本地」下载链使用绝对地址拼接，此时后端须允许该前端的 CORS（见 `CONFIGURATION.md`）。
3. **封面图**：`/api/image-proxy?url=...`，同样受上述根地址逻辑约束。

根目录 [README.md](../README.md) 提供完整 HTTP API 列表。

## 构建与开发命令

```bash
cd frontend
nvm use 22   # 建议
npm install
npm run dev   # http://0.0.0.0:9280
npm run build
```

## 类型检查

```bash
cd frontend
npx vue-tsc -b
```
