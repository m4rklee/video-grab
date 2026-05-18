# 拾影视频下载平台（Video Grab）

自托管的公开视频搜索、下载、AI 总结与视频对话工具。前端使用 Vue 3 + TypeScript + Vite，后端使用 FastAPI，视频解析与下载主要基于 yt-dlp，并对哔哩哔哩、抖音等平台补充了专用链路。

## 一、背景

很多平台的视频无法直接下载，或下载体验不够方便，例如不支持批量保存、清晰度受限、下载后的文件不便于本地归档。拾影视频下载平台希望提供一个轻量、可自托管的视频处理平台，让用户可以从公开链接或关键词搜索开始，完成视频发现、解析、下载、总结和追问。

拾影视频下载平台界面分为视频搜索、视频下载与下载任务三大板块，形成「发现 / 粘贴链接 → 选格式 → 异步下载 → 浏览器保存」闭环；配置 OpenAI 兼容 API 后可对字幕生成 AI 概要、时间轴、思维导图与多轮对话。

请仅下载你拥有保存权利或已获授权的内容，并遵守各平台服务条款与当地法规。本项目不提供 DRM 绕过、加密内容破解或未授权下载能力。

## 二、功能介绍

### 1. 视频搜索

- 支持按关键词搜索 YouTube 和哔哩哔哩公开视频。
- 搜索结果流式返回，边搜索边展示。
- 支持分页、跨页勾选、批量下载和单条视频总结。

### 2. 视频下载

- 支持粘贴公开视频链接，解析标题、封面、时长和可下载格式。
- 支持单条下载和批量下载。
- 支持选择清晰度，哔哩哔哩可使用 legacy 高清链路提升 1080p 成功率。
- 下载任务在后端异步执行，完成后可在浏览器保存文件；批量任务支持 ZIP 打包下载。

### 3. 视频总结

- 配置 OpenAI 兼容 API 后，可以基于字幕生成 AI 总结。
- 支持大纲、分段要点、时间轴和思维导图。
- 总结能力依赖视频字幕；没有字幕或字幕无法提取时，可能无法生成总结。

### 4. 视频对话

- 对已生成总结的视频进行多轮追问。
- 适合快速提炼课程、访谈、演讲、资讯视频中的关键信息。
- 支持通过页面设置填写 OpenAI API Key、Base URL 和模型。

## 三、使用方式

### 本地启动

后端：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8028
```

前端：

```bash
cd frontend
nvm use 22
npm install
npm run dev
```

访问地址：

- 前端：http://localhost:9280
- 后端 OpenAPI：http://localhost:8028/docs

### 配置说明

- 页面右上角“设置”可配置后端 API 地址、OpenAI Key、OpenAI Base URL、模型和哔哩哔哩 Cookie。
- 服务端配置可复制 `backend/.env.example` 为 `backend/.env`。
- 前端开发代理可通过 `frontend/.env.local` 配置 `VITE_API_TARGET`。
- 仓库不会提交真实密钥，`.env` 文件已被忽略。

### 系统依赖

yt-dlp 合并音视频通常需要安装 ffmpeg：

```bash
brew install ffmpeg
```

### 常用验证

```bash
cd backend && pytest
cd frontend && npm run build
```

更多说明可查看：

- [docs/README.md](docs/README.md)
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
- [docs/FRONTEND.md](docs/FRONTEND.md)
- [docs/VIDEO_DOWNLOAD_SUMMARY.md](docs/VIDEO_DOWNLOAD_SUMMARY.md)

## 许可证

[MIT](LICENSE)
