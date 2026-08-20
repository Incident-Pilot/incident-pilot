# Build context MUST be the repo root (not this directory) — the app
# imports `shared.models`, which lives outside services/observation-gateway,
# so both trees need to land in the image. deploy.sh builds this as:
#   docker build -f services/observation-gateway/Dockerfile .
FROM python:3.11-slim

WORKDIR /app

COPY services/observation-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY services/observation-gateway/app ./app

RUN useradd --no-create-home --uid 10001 gateway
USER gateway

ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
