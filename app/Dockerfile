# ---------- Stage 1: build wheels ----------
FROM python:3.12-alpine AS builder

WORKDIR /build
COPY requirements.txt .

# build deps only needed for compiling wheels, not present in final image
RUN apk add --no-cache gcc musl-dev libffi-dev postgresql-dev \
    && pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /build/wheels -r requirements.txt

# ---------- Stage 2: runtime ----------
FROM python:3.12-alpine AS runtime

LABEL org.opencontainers.image.title="voting-app-frontend" \
      org.opencontainers.image.description="Cricket vs Football voting frontend"

# runtime-only shared lib needed by psycopg2-binary
RUN apk add --no-cache libpq

# non-root user/group
RUN addgroup -S appgroup -g 1001 && adduser -S appuser -G appgroup -u 1001

WORKDIR /app

COPY --from=builder /build/wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY --chown=appuser:appgroup app.py .
COPY --chown=appuser:appgroup templates/ ./templates/
COPY --chown=appuser:appgroup static/ ./static/

USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1

# gunicorn for production-grade WSGI serving (not flask dev server)
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", "--timeout", "30", "app:app"]
