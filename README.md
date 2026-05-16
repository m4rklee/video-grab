# VideoGrab AI

VideoGrab AI 是一个轻量 MVP：前端使用 Vue 3 + Vite + TypeScript，后端使用 FastAPI，下载能力直接封装 yt-dlp。首版支持单链接解析、选择格式、服务器临时中转下载，再由浏览器保存到本地。

## 实现说明（维护文档）

解析与下载的后端链路（含抖音多路径回退、CDN 请求头等）见 **[docs/VIDEO_DOWNLOAD_SUMMARY.md](docs/VIDEO_DOWNLOAD_SUMMARY.md)**。

## 功能范围

- 输入公开视频链接并解析标题、封面、时长和平台信息
- 推荐最佳画质，也支持选择常见清晰度或仅音频
- 后端临时保存下载文件，完成后浏览器保存到本地
- 无数据库、无账号、无真实 Stripe，保留 Pro 能力展示入口
- 不支持绕过 DRM、加密内容或无授权内容下载

## 本地启动

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
npm install
npm run dev
```

访问：

- 前端：http://localhost:9280
- 后端文档：http://localhost:8028/docs

## 系统依赖

yt-dlp 下载和合并音视频时通常需要 ffmpeg。macOS 可使用：

```bash
brew install ffmpeg
```

前端页面会调用 `GET /api/health` 展示 yt-dlp 和 ffmpeg 的可用状态。

## API

- `GET /api/health`
- `POST /api/video/probe`
- `POST /api/downloads`
- `GET /api/downloads/{job_id}`
- `GET /api/downloads/{job_id}/file`

## 验收建议

1. 打开首页，确认 375px、768px、桌面宽屏布局都不溢出。
2. 粘贴一个公开视频链接，确认能解析出视频信息和格式列表。
3. 选择推荐格式并开始下载，确认进度条更新。
4. 下载完成后点击“保存到本地”，确认浏览器能保存文件。

## 测试

后端：

```bash
cd backend
pytest
```

前端：

```bash
cd frontend
npm run build
```
