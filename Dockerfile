FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    DB_PATH=/data/timeline.db

WORKDIR /app
COPY app/ /app/

# 跨地区时间线依赖系统 IANA 时区库（zoneinfo 读取 /usr/share/zoneinfo）。
# 应用本身仍仅使用 Python 标准库，无任何 pip 第三方依赖。
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/reviews')"

# 启动时自动初始化数据库（建表），随后提供服务
CMD ["python", "server.py"]
