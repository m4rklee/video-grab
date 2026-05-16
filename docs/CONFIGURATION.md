# 配置说明（端口与环境变量）

本文与根目录 [README.md](../README.md)、[backend/.env.example](../backend/.env.example) 及前端 [apiConfig.ts](../frontend/src/apiConfig.ts) 保持一致。

## 端口与代理

| 服务 | 默认端口 | 说明 |
|------|----------|------|
| 前端开发（Vite） | `9280` | `strictPort`，见 `frontend/vite.config.ts` |
| 后端 API（uvicorn） | `8028` | 与 Vite `proxy['/api']` 默认目标一致 |

浏览器访问：`http://localhost:9280`。API 走同源 `/api/...`，由 Vite 代理到后端。

开发时若后端端口不同，可在 `frontend/.env.local` 设置（可选）：

```bash
VITE_API_TARGET=http://127.0.0.1:YOUR_PORT
```

## 前端运行环境

- **Node.js**：≥ 22.12（见 `frontend/package.json` 的 `engines`）
- 示例：`nvm use 22` 后 `npm install`、`npm run dev` / `npm run build`

## 浏览器「设置」与请求头

在页面 **设置** 中保存的配置位于 **localStorage**，随 API 请求发送（仅发往你填写的后端）：

| 设置项 | 请求头 | 说明 |
|--------|--------|------|
| 后端 API 地址 | （改变 fetch 根 URL） | 留空则用当前站点 `/api` |
| OpenAI API Key | `X-OpenAI-Api-Key` | 优先于服务端 `OPENAI_API_KEY` |
| OpenAI Base URL | `X-OpenAI-Base-Url` | 可选 |
| 总结模型 | `X-Summarize-Model` | 可选 |
| 哔哩哔哩 Cookie | `X-Bilibili-Cookie` | 可选；搜索与高清下载 |

实现见 [`backend/app/llm_runtime.py`](../backend/app/llm_runtime.py)、[`backend/app/bilibili_cookie.py`](../backend/app/bilibili_cookie.py)。

## 后端环境变量

复制 [`backend/.env.example`](../backend/.env.example) 为 `backend/.env`（或仓库根 `.env`；重复键以 `backend/.env` 为准）。

| 变量 | 作用 |
|------|------|
| `OPENAI_API_KEY` | AI 总结 / 对话（未在浏览器设置时生效） |
| `OPENAI_BASE_URL` | OpenAI 兼容 API Base URL |
| `SUMMARIZE_MODEL` | 模型名（默认 `gpt-4o-mini`） |
| `BILIBILI_COOKIE` / `BILIBILI_SESSDATA` | B 站登录态（未在浏览器设置时生效） |
| `YOUTUBE_DATA_API_KEY` | 可选；YouTube 字幕辅助 |
| `CORS_ORIGINS` | 额外 CORS 源（逗号分隔） |

字幕与总结链路见 [VIDEO_DOWNLOAD_SUMMARY.md](./VIDEO_DOWNLOAD_SUMMARY.md)。

## CORS

后端默认允许本机源（`localhost` / `127.0.0.1` 任意端口）。静态页部署在其它域名时，请设置 **`CORS_ORIGINS`**，并在浏览器设置中填写 API 根地址。
