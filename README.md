# 图文生产平台 qvp3（小红书风格图文批量生产）

> 当前版本：**v0.1.2**（分支 `release/v0.1.2`，tag `v0.1.2`）
> 创作网关：**dsh_serve**（dsh harness 薄层，OpenAI 兼容 :8901）｜ 部署目标：**本地 8005 / 服务器 8005**

## 这是什么

导入选题 Query → AI 取证/写正文/分 6 页文案/生成 6 张竖版卡片图（3:4）→
机器质检（OCR 对撞 + VL 逐字校验）→ 三道人工关卡（文字核查/参考图/审图）→ 签发导出。
三种模式：`general`（通用科普）/ `single`（单品实测，图生图）/ `compare`（双主体对比）。

## 快速开始（本地开发调试）

**新接手请先读 [`docs/交接-v0.1.2-开发调试.md`](docs/交接-v0.1.2-开发调试.md)**——
含完整搭建命令、环境变量清单、调试工具、踩坑记录、发布流程与红线。
密钥与服务器信息不在仓库中，向交接人索取单独交接文件。

极简路径：

```bash
git clone -b release/v0.1.2 git@github.com:zhangleifire-coder/qvp3.git
cd qvp3/code
# 1) 准备 PostgreSQL(5433) 与 OpenSERP(7001) 容器（命令见交接文档 §3）
# 2) 将密钥写入 .env（交接文件 §1）
# 3) 构建并启动
docker build -f deploy/Dockerfile.dsh -t qvp3-app:latest .
# 启动命令见交接文档 §3（注意 DSH_SERVE_BASE_URL 与 MCP_* 必须显式传入）
curl http://127.0.0.1:8005/healthz   # {"status":"ok"}
```

## 仓库结构

```
src/        FastAPI 后端（api/pipeline/gateway/services/stream/models）
dsh_serve/  创作网关薄层（内嵌 dsh harness，拉起 qvp_mcp 工具子进程）
qvp_mcp/    MCP 工具服务器（web_search/image_search/generate_images/ocr_image）
static/     前端（Vue3 CDN 版，无构建）+ 交接文档在线版
skills/     提示词 skill 包（提示词单一事实来源）
migrations/ 编号 SQL 迁移（幂等，启动自动应用）
tests/      pytest 单测/集成（容器内运行，见交接文档 §5）
deploy/     Dockerfile.dsh / compose / 入口脚本
docs/       交接文档、审图 SOP、历史设计档案
```

## 版本要点（v0.1.2）

- 生图提示词铁律：图上文字仅限给定文案 + 字形/渲染质量硬约束 + 字号≥边框 7% 且统一
- 质检链：VL 逐字校验（`verify_page_glyphs`）自动拦错字/变形/糊图并重生成
- nanobot 已下线：统一 `dsh_client` + `DSH_SERVE_*` 配置
- 审图操作 SOP：[`docs/审图操作说明-错字糊字处理.md`](docs/审图操作说明-错字糊字处理.md)

## 历史

- v0.1.2（2026-09-18）本版本；更早历史见 `docs/` 档案与 git log。
