# 产能验证平台 — Docker 部署包

一条命令启动全部服务（应用 + PostgreSQL + Redis + OpenSERP）。

## 启动

```bash
docker compose up -d --build
```

首次启动会自动建表（21 张）+ 初始化三个账号，幂等可重复执行。

## 访问

- 本机：`http://localhost:8000`
- 登录账号：`张三` / `李四` / `王五`（密码均为 `1qaz@WSX`）

## 常用命令

```bash
docker compose logs -f app   # 查看应用日志
docker compose down          # 停止
docker compose down -v       # 停止并清空数据
```

## 说明

- API key 已配置在 `.env`，无需手动设置。
- 依赖版本由 `requirements.txt` 固定（来自 uv.lock），构建可复现。
- 默认用清华 pip 镜像源加速；如需切换：

```bash
docker compose build --build-arg PIP_INDEX_URL=https://pypi.org/simple
```
