# 使用可覆盖的基础镜像，便于本地 Docker 环境按需切换镜像源
ARG BASE_IMAGE=python:3.11-alpine3.21
FROM ${BASE_IMAGE}

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装系统依赖（如果需要）
# RUN apk add --no-cache \
#     gcc \
#     musl-dev \
#     libffi-dev \
#     openssl-dev

# 复制requirements文件并安装Python依赖
COPY requirements.txt .
RUN if [ -n "$PIP_TRUSTED_HOST" ]; then \
        python -m pip install --no-cache-dir --index-url "$PIP_INDEX_URL" --trusted-host "$PIP_TRUSTED_HOST" -r requirements.txt; \
    else \
        python -m pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt; \
    fi
RUN python -c "import fastapi, httpx, aioimaplib, pydantic, requests"

# 复制应用代码
COPY main.py .
COPY static/ ./static/
COPY docker-entrypoint.sh .

# 设置启动脚本权限
RUN chmod +x docker-entrypoint.sh

# 创建数据目录用于持久化存储
RUN mkdir -p /app/data && chown 777 /app/data

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/api/auth/state', timeout=10)" || exit 1

# 启动命令
CMD ["./docker-entrypoint.sh"] 
