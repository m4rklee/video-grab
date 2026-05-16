# 拾影视频下载器（Video Grab）

> 自托管的公开视频 **关键词搜索**、**多平台解析下载** 与 **AI 字幕总结** 工具。前端 Vue 3 + TypeScript，后端 FastAPI，下载与解析基于 yt-dlp（抖音等有专用补充链路）。

**技术栈**：Vue 3 · TypeScript · Vite · FastAPI · yt-dlp · ffmpeg · NDJSON 流式搜索 · Markmap

**核心亮点**

- **流式搜索**：`POST /api/search` 返回 NDJSON，YouTube / 哔哩哔哩结果边搜边出
- **B 站高清**：classic playurl + ffmpeg 合并，单条与批量均支持 1080p 档位（Cookie 可提升成功率）
- **批量下载**：勾选 → 画质确认 → 任务页进度 → 完成后 **ZIP** 打包
- **浏览器设置**：API 地址、OpenAI Key、B 站 Cookie 保存在本机，经请求头发往自建后端
- **测试**：后端 pytest 50+ 用例，GitHub Actions 持续集成

**演示**：克隆仓库后按下方「本地启动」运行；前端 http://localhost:9280 ，后端 OpenAPI http://localhost:8028/docs 。

**合规**：请仅下载你拥有保存权利或已获授权的内容；遵守各平台服务条款与当地法规。本项目不提供 DRM 绕过或未授权下载能力。

---

**拾影视频下载器**（英文名 Video Grab；「拾影」意为拾起、留存影像）界面分为 **视频搜索**、**视频下载** 与 **下载任务** 三大板块，形成「发现 / 粘贴链接 → 选格式 → 异步下载 → 浏览器保存」闭环；配置 OpenAI 兼容 API 后可对字幕生成 **AI 概要、时间轴、思维导图与多轮对话**。

## 文档索引

| 文档 | 内容 |
|------|------|
| [docs/README.md](docs/README.md) | **文档导航**、阅读顺序、分工约定 |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | 端口、Vite 代理、环境变量、CORS、浏览器设置与请求头 |
| [docs/FRONTEND.md](docs/FRONTEND.md) | 前端结构、三 Tab、设置弹窗、AI Tab、导图导出 |
| [docs/VIDEO_DOWNLOAD_SUMMARY.md](docs/VIDEO_DOWNLOAD_SUMMARY.md) | 后端解析与下载链路（含抖音、字幕与总结） |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献与本地开发 |
| [SECURITY.md](SECURITY.md) | 自托管部署与安全说明 |

**维护约定**：解析、下载细节以 [docs/VIDEO_DOWNLOAD_SUMMARY.md](docs/VIDEO_DOWNLOAD_SUMMARY.md) 为准；界面见 [docs/FRONTEND.md](docs/FRONTEND.md)；配置见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)。

## 界面与配置（摘要）

- **顶栏**：品牌名；**视频搜索** / **视频下载** / **下载任务**；右侧 **设置**。
- **视频搜索**：YouTube / 哔哩哔哩、流式加载、分页、跨页勾选、批量下载、单条 AI 总结。
- **下载任务**：批量总进度、子项状态、ZIP 下载。
- **视频下载**：链接解析、格式选择、本地下载、可选 AI 总结。
- **设置**（保存在浏览器 localStorage）：后端 API 地址（可选）、OpenAI API Key / Base URL / 模型、哔哩哔哩 Cookie（可选）。也可在服务端 `backend/.env` 配置（见下）。

## 功能范围

- **关键词搜索**（YouTube / 哔哩哔哩）：NDJSON 流式结果、分页、跨页勾选、批量下载、单条 AI 总结
- 粘贴公开视频链接，解析标题、封面、时长与格式列表
- **AI 总结**（需 OpenAI 兼容 API）：大纲、分段要点、思维导图、多轮对话（依赖字幕）
- 推荐高清档位（含 B 站 legacy 1080p）与 yt-dlp 自动画质
- 无数据库、无账号；批量任务存于后端内存（重启后丢失）
- 不支持 DRM、加密或未授权内容

## 本地启动

**后端**（Python 3，建议虚拟环境）：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8028
```

**前端**（Node ≥ 22）：

```bash
cd frontend
nvm use 22
npm install
npm run dev
```

访问：

- 前端：http://localhost:9280
- 后端 OpenAPI：http://localhost:8028/docs

### 配置方式

| 方式 | 适用 |
|------|------|
| 页面 **设置** | API 根地址、OpenAI Key、B 站 Cookie（本机浏览器） |
| `backend/.env` | 服务端默认密钥与 CORS（复制 [backend/.env.example](backend/.env.example)） |
| `frontend/.env.local` | 仅开发：`VITE_API_TARGET` 指定 Vite 代理目标（可选） |

- 仓库**不**包含密钥；`.env` 已在 `.gitignore` 中忽略。
- 前后端分离部署时，在设置中填写 API 地址，并在后端配置 `CORS_ORIGINS`。
- 本地开发一般 **留空 API 地址**，使用 Vite 将 `/api` 代理到 `127.0.0.1:8028`。

## 系统依赖

yt-dlp 合并音视频通常需要 **ffmpeg**：

```bash
brew install ffmpeg   # macOS
```

`GET /api/health` 会报告 yt-dlp、ffmpeg 与 AI 总结是否就绪（`summarize_llm_ready`）。

## API（摘要）

完整契约见 http://localhost:8028/docs 。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 依赖与 LLM 就绪 |
| POST | `/api/search` | 关键词搜索（NDJSON 流） |
| POST | `/api/video/probe` | 解析链接与格式列表 |
| POST | `/api/download-batches` | 创建批量任务（`urls`, `quality_preset`） |
| GET | `/api/download-batches` | 最近批量任务列表 |
| GET | `/api/download-batches/{id}` | 批量进度与子项 |
| GET | `/api/download-batches/{id}/zip` | 下载 ZIP |
| POST | `/api/downloads` | 创建单条下载 |
| GET | `/api/downloads/{job_id}` | 任务状态 |
| GET | `/api/downloads/{job_id}/file` | 下载文件 |
| GET | `/api/image-proxy` | 封面代理 |
| POST | `/api/summarize` | 创建 AI 总结 |
| GET | `/api/summarize/{job_id}` | 总结状态与结果 |
| POST | `/api/summarize/{job_id}/chat` | 多轮追问 |

`POST /api/downloads/batch` 已废弃（410），请使用 `/api/download-batches`。

## 验收建议

1. 三 Tab 切换正常；搜索流式加载与分页勾选正常。
2. **下载选中** → 画质确认 → **下载任务** 页进度与 ZIP。
3. **视频下载** 粘贴链接，解析并下载；B 站可选 1080p 高清解析档位。
4. **设置** 填写 OpenAI Key 后，AI 总结与搜索页「视频总结」可用。
5. 修改 API 地址后健康检查仍正常。

## 测试

```bash
cd backend && pytest
cd frontend && npm run build
```

## 许可证

[MIT](LICENSE)
