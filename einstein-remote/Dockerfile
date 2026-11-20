# EINSTEIN REMOTE — servidor (painel web + API + tempo real)
#   docker build -t einstein-remote .
#   docker compose up -d          (com HTTPS automático via Caddy — ver docker-compose.yml)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EINSTEIN_DATA_DIR=/data \
    EINSTEIN_WEB_DIR=/app/web

WORKDIR /app
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY server/ server/
COPY web/ web/

# usuário sem privilégios; dados (banco, chaves) só no volume /data
RUN useradd --system --uid 10001 --home-dir /data einstein && mkdir -p /data && chown einstein:einstein /data
USER einstein
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# --proxy-headers: atrás do Caddy/Nginx, usa o IP real do visitante (X-Forwarded-For) para
# limitar tentativas de login e registrar a auditoria — confiando só no proxy (FORWARDED_ALLOW_IPS)
CMD ["uvicorn", "app.main:app", "--app-dir", "server", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
