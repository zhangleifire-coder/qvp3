FROM python:3.12-slim

WORKDIR /app

# 先复制依赖清单，利用 Docker 层缓存
COPY pyproject.toml requirements.txt ./
COPY src/ ./src/
COPY static/ ./static/
COPY migrations/ ./migrations/
COPY init_db.py ./
COPY docker-entrypoint.sh ./

# 安装依赖（版本由 uv.lock 固定，可复现；镜像源可用 --build-arg 覆盖）
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt \
    && chmod +x docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
