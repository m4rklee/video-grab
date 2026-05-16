# 文档导航

拾影视频下载器（Video Grab）的正式说明集中于此目录与根目录 [README.md](../README.md)。

## 阅读顺序（推荐）

| 顺序 | 文档 | 说明 |
|------|------|------|
| 1 | [README.md](../README.md) | 项目定位、作品集摘要、启动、API、测试 |
| 2 | [CONFIGURATION.md](./CONFIGURATION.md) | 端口、代理、环境变量、浏览器设置与请求头 |
| 3 | [FRONTEND.md](./FRONTEND.md) | 前端目录、三 Tab、设置、AI 与导图导出 |
| 4 | [VIDEO_DOWNLOAD_SUMMARY.md](./VIDEO_DOWNLOAD_SUMMARY.md) | 后端解析、下载、批量、字幕与 AI |

## 文档分工

| 主题 | 以哪份为准 |
|------|------------|
| 用户能力、启动、API 一览 | `README.md` |
| 环境变量、代理、跨域、设置页 | `CONFIGURATION.md` |
| 页面结构、交互、前端模块 | `FRONTEND.md` |
| yt-dlp / 抖音 / 转写 / LLM 链路 | `VIDEO_DOWNLOAD_SUMMARY.md` |

## 环境变量入口

- 模板：[backend/.env.example](../backend/.env.example)
- 浏览器设置实现：[frontend/src/apiConfig.ts](../frontend/src/apiConfig.ts)

密钥勿提交 Git；浏览器内 Key 仅发往自建后端。
