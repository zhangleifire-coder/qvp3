# 图文生产平台 qvp3（小红书风格图文批量生产）

> 当前版本：**v0.1.4**（分支 `release/v0.1.4`，开发中——基准：[`docs/开发规划-模板对齐与差距收敛-20260922.md`](docs/开发规划-模板对齐与差距收敛-20260922.md)）
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
git clone -b release/v0.1.4 git@github.com:zhangleifire-coder/qvp3.git
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

## 版本要点（v0.1.4 · 开发中）

- 本版本按《开发规划-模板对齐与差距收敛-20260922.md》执行五阶段：
  **P0 退役收口**（staged+混合合成转正为默认路径，删临时 API/孤儿模块）
  → **P1 提示词 SSOT 回写**（以《生图通用Prompt模板-20260922-最终版.md》八条铁律为唯一基准）
  → **P2 结构化分页文案 + 页数 5/6 可配** → **P3 跨页质检** → **P4 基准与发版**
- 生产路径事实标准：staged 四节点 + 混合合成（程序渲染中文文字 + AI 无文字画面，
  compose v6），config 默认值已与 .env 对齐；直连路径保留为回退与 mock 联调链

## 历史

- v0.1.4（2026-09-22 起）模板对齐升级，开发中
- v0.1.3（2026-09-21，tag `v0.1.3`）海报混合合成链路 compose v2-v6、风格库三库合成、五项提速
- v0.1.2（2026-09-18）提示词铁律底座、VL 逐字校验、dsh 网关转正；更早历史见 `docs/` 档案与 git log。
