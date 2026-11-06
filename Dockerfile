# ============================================================================
# 每日答题系统 · 生产镜像（Flask + Gunicorn + MySQL）
# 构建：docker build -t daily-quiz:latest .
# 推荐：docker compose up -d --build（一键同时拉起 MySQL 与 Web）
# ============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 依赖单独成层，源码变动时可命中 pip 缓存层
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 应用源码（.dockerignore 已排除 .git / .venv / .env / __pycache__ / *.db / 测试脚本）
COPY . .

# 非 root 运行：只需 /app 可写（SQLite 回退模式会写 data/，MySQL 模式不写文件）
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

# 容器内固定走 MySQL；具体连接参数由 docker-compose.yml / 环境变量注入
ENV DB_BACKEND=mysql \
    DB_CONNECT_RETRIES=30 \
    DB_CONNECT_RETRY_DELAY=2 \
    FLASK_RUN_HOST=0.0.0.0

EXPOSE 5000

# /login 是免登录页面，适合做存活探针
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/login', timeout=3)" || exit 1

# --preload：在 master 进程里先完成 init_db() / 默认账号播种，再 fork worker，
# 避免多个 worker 并发建表或并发插入默认账号触发唯一键冲突。
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "2", \
     "--preload", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
