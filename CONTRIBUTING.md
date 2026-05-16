# 贡献指南

感谢关注 **拾影视频下载器（Video Grab）**。

## 本地开发

1. 克隆仓库并阅读根目录 [README.md](README.md)。
2. **后端**：`cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
3. **前端**：`cd frontend && npm install`（建议 Node ≥ 22）
4. 启动：`uvicorn app.main:app --reload --host 0.0.0.0 --port 8028` 与 `npm run dev`

## 提交前检查

```bash
cd backend && pytest
cd frontend && npm run build
```

## 提交流程

1. 从 `main` 拉取最新代码并创建分支。
2. 保持改动聚焦；解析/下载行为变更请同步 [docs/VIDEO_DOWNLOAD_SUMMARY.md](docs/VIDEO_DOWNLOAD_SUMMARY.md)。
3. 勿提交 `.env`、`backend/downloads/` 或任何密钥。
4. 发起 Pull Request 并说明动机与测试方式。

## 报告问题

请使用 GitHub Issues，附上复现步骤、环境与期望行为。安全相关问题见 [SECURITY.md](SECURITY.md)。
