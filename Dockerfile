# 《千古一局：暗合推演》服务端容器镜像 (Dockerfile)

# 包含：Python 3.12-slim + FastAPI + Uvicorn + 确定性状态机与夜间 BSP 推演引擎

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 

PYTHONUNBUFFERED=1 

TZ=Asia/Shanghai

WORKDIR /app/server

# 安装基础健康检查工具

RUN apt-get update && apt-get install -y --no-install-recommends 

curl 

&& rm -rf /var/lib/apt/lists/\*

# 安装服务端依赖

COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt

# 拷贝服务端核心模块与共享契约

COPY server/\*.py /app/server/
COPY shared-schemas/mock_chibi_day2_replay.json /app/server/mock_chibi_day2_replay.json

# 创建非 root 运行用户，遵循容器安全基线

RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 

CMD curl -fsS https://www.google.com/search?q=http://127.0.0.1:8000/api/leaderboard || exit 1

CMD \["uvicorn", "app_server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"\]