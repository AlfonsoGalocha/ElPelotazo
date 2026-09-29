# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencias del sistema para psycopg (postgres), compilacion de wheels,
# y Node.js/npm (necesarios para el CLI `claude`, que usa
# AGENT_LLM_PROVIDER=claude_code -- backend/app/agent/llm.py -- para pagar
# el LLM con tu suscripcion Claude Pro/Max en vez de una API key de pago).
# El nodejs de los repos de Debian basta (no hace falta NodeSource): el
# CLI no necesita una version de Node muy reciente.
# ffmpeg: decodifica el audio webm/opus que graba MediaRecorder en el
# navegador antes de pasarselo a faster-whisper (backend/app/agent/transcription.py,
# Fase 7 de voz -- transcripcion propia porque el reconocimiento nativo del
# navegador falla en Brave, ver comentario en pyproject.toml).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl nodejs npm ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code

COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --no-cache-dir -e .

COPY scripts ./scripts

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
