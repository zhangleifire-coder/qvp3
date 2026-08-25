# qvp2 独立栈应用镜像：后端 + Nanobot + qvp_mcp 同容器（127.0.0.1 内部互联）
FROM python:3.12-slim

ENV PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先装依赖层（代码变动不触发重装）
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && pip install nanobot-ai==0.3.0

# 代码 + 部署脚本（.env 由 compose env_file 运行时注入，不打进镜像）
COPY . .
RUN chmod +x deploy/entrypoint.sh && mkdir -p static/generated exports

EXPOSE 8003
ENTRYPOINT ["deploy/entrypoint.sh"]
