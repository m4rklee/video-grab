# Video Grab

> A self-hosted video search, download, AI summary, and video chat platform.

拾影视频下载平台是一个自托管的公开视频搜索、下载、AI 总结与视频对话工具。项目包含 Vue 3 前端和 FastAPI 后端，视频解析与下载主要基于 `yt-dlp`，并针对哔哩哔哩、抖音等平台补充专用链路。

## Highlights

- **Search and download in one flow**: 支持搜索公开视频并直接选择下载，减少“先找再粘贴”的操作成本。
- **Batch download**: 支持跨页勾选、批量任务和 ZIP 打包下载。
- **AI video summary**: 基于字幕生成大纲、分段摘要、时间轴和思维导图。
- **Video chat**: 可围绕视频内容进行问答。
- **Streaming search UX**: 搜索结果边返回边展示。
- **Self-hosted stack**: 前后端分离，适合本地或私有服务器部署。

## Demo

视频搜索：

![视频搜索](pics/video_search.png)

![视频搜索示例](pics/video_search_example1.png)

视频下载：

![视频下载](pics/video_download.png)

AI 总结：

![AI 视频总结](pics/ai_sum_summary.png)

![AI 总结时间轴](pics/ai_sum_timeline.png)

![AI 总结导图](pics/ai_sum_mindmap.png)

AI 视频对话：

![AI 视频对话](pics/ai_sum_dialogue.png)

## Architecture

```text
Vue Frontend
    |
    v
FastAPI Backend
    |
    +--> video search
    +--> yt-dlp / platform-specific parsers
    +--> download tasks
    +--> subtitle extraction
    +--> AI summary and video chat
```

## Features

| Module | Description |
|---|---|
| **Video Search** | 按关键词搜索 YouTube、哔哩哔哩等公开视频，搜索结果流式返回。 |
| **Video Download** | 粘贴链接后解析标题、封面、时长和格式，支持单条下载。 |
| **Batch Download** | 支持跨页勾选、批量下载和 ZIP 打包。 |
| **AI Summary** | 配置 OpenAI-compatible API 后，基于字幕生成摘要、时间轴和思维导图。 |
| **Video Chat** | 围绕视频字幕和内容进行问答。 |
| **Configuration Docs** | `docs/` 中保留前后端配置、下载链路和安全说明。 |

## Tech Stack

- **Frontend**: Vue 3, TypeScript, Vite, lucide-vue-next, D3, markmap
- **Backend**: FastAPI, Uvicorn, yt-dlp, curl-cffi
- **AI**: OpenAI-compatible API for summaries and chat
- **Packaging**: Node.js, Python virtualenv, optional self-hosted deployment

## Quick Start

```bash
git clone https://github.com/m4rklee/video-grab.git
cd video-grab
```

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 9279
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

前端默认端口由 `frontend/package.json` 控制：

```text
http://127.0.0.1:9280
```

## Usage

1. 在搜索页输入关键词，选择需要下载的视频。
2. 或在下载页粘贴公开视频链接。
3. 选择格式和清晰度后创建下载任务。
4. 若配置了 AI API，可对视频字幕生成总结、时间轴、思维导图并进行问答。

更多配置见：

```text
docs/CONFIGURATION.md
docs/FRONTEND.md
docs/VIDEO_DOWNLOAD_SUMMARY.md
```

## Project Structure

```text
backend/                 # FastAPI service, video parsing, download and AI summary APIs
frontend/                # Vue 3 / Vite web app
docs/                    # Configuration and implementation notes
pics/                    # README screenshots
```

## Safety & Limitations

- 请只下载你有权访问和保存的公开视频内容。
- 不同平台的解析能力会随站点策略变化而变化。
- AI 总结依赖字幕质量和模型配置，结果需要人工核对。
- 大文件下载建议在本地或私有服务器中运行，注意磁盘空间。

## Roadmap

- Add persistent download history.
- Improve platform-specific parsers.
- Add task queue observability.
- Add deployment examples for NAS or VPS.
