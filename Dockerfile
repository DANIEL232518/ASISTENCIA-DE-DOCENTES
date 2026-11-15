# ============================================================
#  PWA Control de Asistencia Docente  ·  Multi-etapa
#  Etapa 1: compila Tailwind CSS + vendoriza html5-qrcode (offline)
#  Etapa 2: runtime Python 3.11 + FastAPI
# ============================================================

# ---------- Etapa 1: Frontend build ----------
FROM node:20-alpine AS frontend
WORKDIR /build

COPY frontend/package.json ./
RUN npm install --no-audit --no-fund

COPY frontend/tailwind.config.js ./frontend/tailwind.config.js
COPY app/static/css/input.css ./app/static/css/input.css
COPY app/templates ./app/templates
COPY app/static/js ./app/static/js

# Compila el CSS final (solo se usan las clases realmente presentes)
RUN npx tailwindcss -c frontend/tailwind.config.js \
      -i app/static/css/input.css \
      -o /out/tailwind.css --minify

# Vendoriza el escáner QR (funciona sin internet en el navegador)
RUN mkdir -p /out \
 && F=$(ls node_modules/html5-qrcode/html5-qrcode*.js | head -n 1) \
 && cp "$F" /out/html5-qrcode.js \
 && echo "html5-qrcode OK: $F"

# ---------- Etapa 2: Backend ----------
FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=America/Lima \
    APP_HOME=/app

WORKDIR $APP_HOME

# Fuentes para las credenciales PNG + tzdata para America/Lima
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY --from=frontend /out/tailwind.css $APP_HOME/app/static/css/tailwind.css
COPY --from=frontend /out/html5-qrcode.js $APP_HOME/app/static/vendor/html5-qrcode.js

RUN mkdir -p $APP_HOME/app/static/uploads/docentes \
             $APP_HOME/app/static/credenciales \
             $APP_HOME/app/static/img

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=6s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
