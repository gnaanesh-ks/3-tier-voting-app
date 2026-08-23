# ---------- Stage 1: build wheels ----------
FROM python:3.12-alpine AS builder

WORKDIR /build
COPY requirements.txt .

RUN apk add --no-cache gcc musl-dev postgresql-dev \
    && pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /build/wheels -r requirements.txt

# ---------- Stage 2: runtime ----------
FROM python:3.12-alpine AS runtime

LABEL org.opencontainers.image.title="voting-app-worker" \
      org.opencontainers.image.description="Cricket vs Football vote processing worker"

# runtime-only shared lib needed by psycopg2-binary
RUN apk add --no-cache libpq

RUN addgroup -S appgroup -g 1001 && adduser -S appuser -G appgroup -u 1001

WORKDIR /app

COPY --from=builder /build/wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY --chown=appuser:appgroup worker.py .

USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Simple liveness check: verify the process is running by touching a heartbeat file.
# (Kubernetes probes for this workload are exec-based; see k8s/worker-deployment.yaml)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD pgrep -f worker.py || exit 1

CMD ["python", "worker.py"]
