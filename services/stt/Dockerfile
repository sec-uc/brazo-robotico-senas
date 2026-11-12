# Imagen del microservicio stt. Contexto de build: raíz del monorepo.
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY libs/rsp_common /srv/libs/rsp_common
RUN pip install --no-cache-dir /srv/libs/rsp_common
COPY services/stt/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt
# Motor opcional: docker compose -f docker-compose.yml -f docker-compose.whisper.yml up --build stt
ARG WITH_WHISPER=0
COPY services/stt/requirements-whisper.txt /srv/requirements-whisper.txt
RUN if [ "$WITH_WHISPER" = "1" ]; then pip install --no-cache-dir -r /srv/requirements-whisper.txt; fi
ARG WITH_OPENAI_WHISPER=0
COPY services/stt/requirements-whisper-openai.txt /srv/requirements-whisper-openai.txt
RUN if [ "$WITH_OPENAI_WHISPER" = "1" ]; then apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir -r /srv/requirements-whisper-openai.txt; fi
COPY services/stt/app /srv/app
RUN useradd -r -m appuser && mkdir -p /data/hf && chown -R appuser /data /srv
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 CMD curl -fs http://localhost:8000/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
