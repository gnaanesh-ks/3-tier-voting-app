"""
Cricket vs Football - Voting Frontend
Writes votes to Redis for high-concurrency ingestion.
The background worker later drains Redis into PostgreSQL.
"""
import os
import socket
import logging

import redis
import psycopg2
from psycopg2 import pool as pg_pool
from flask import Flask, render_template, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vote-app")

app = Flask(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
VOTE_QUEUE_KEY = os.getenv("VOTE_QUEUE_KEY", "votes")
HOSTNAME = socket.gethostname()

# A resilient connection pool - do NOT crash the pod if Redis is briefly unavailable.
redis_pool = redis.ConnectionPool(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
    max_connections=50,
)


def get_redis():
    return redis.Redis(connection_pool=redis_pool)


PG_HOST = os.getenv("POSTGRES_HOST", "postgres")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB = os.getenv("POSTGRES_DB", "votingdb")
PG_USER = os.getenv("POSTGRES_USER", "voting_user")
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")

try:
    # Eagerly opens `minconn` connections - fine here since compose/k8s only
    # start this service after Postgres reports healthy. If that ever isn't
    # true, fall back to no pool rather than crash-looping the pod.
    pg_conn_pool = pg_pool.SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD,
        connect_timeout=3,
    )
except psycopg2.Error as exc:
    logger.error("Could not initialize Postgres pool at startup: %s", exc)
    pg_conn_pool = None


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", hostname=HOSTNAME)


@app.route("/vote", methods=["POST"])
def vote():
    choice = request.form.get("vote") or (request.json or {}).get("vote")
    if choice not in ("cricket", "football"):
        return jsonify({"error": "invalid vote, must be 'cricket' or 'football'"}), 400

    try:
        r = get_redis()
        # LPUSH is O(1) - perfect for absorbing bursty traffic; worker does the heavy lifting.
        r.lpush(VOTE_QUEUE_KEY, choice)
    except redis.exceptions.RedisError as exc:
        logger.error("Redis push failed: %s", exc)
        return jsonify({"error": "vote service temporarily unavailable"}), 503

    return jsonify({"status": "accepted", "vote": choice, "served_by": HOSTNAME}), 202


@app.route("/results", methods=["GET"])
def results():
    """Reads consolidated counts from the `vote_results` view the worker keeps updated."""
    if pg_conn_pool is None:
        return jsonify({"error": "results temporarily unavailable"}), 503

    conn = None
    try:
        conn = pg_conn_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("SELECT candidate, vote_count, COALESCE(pct_share, 0) FROM vote_results;")
            rows = cur.fetchall()
        data = [
            {"candidate": candidate, "votes": count, "pct": float(pct)}
            for candidate, count, pct in rows
        ]
        return jsonify({"results": data}), 200
    except psycopg2.Error as exc:
        logger.error("Postgres query failed: %s", exc)
        return jsonify({"error": "results temporarily unavailable"}), 503
    finally:
        if conn is not None:
            pg_conn_pool.putconn(conn)


@app.route("/healthz", methods=["GET"])
def healthz():
    """Liveness: process is up. Does not depend on Redis."""
    return jsonify({"status": "ok"}), 200


@app.route("/readyz", methods=["GET"])
def readyz():
    """Readiness: only serve traffic if we can actually reach Redis."""
    try:
        get_redis().ping()
        return jsonify({"status": "ready"}), 200
    except redis.exceptions.RedisError:
        return jsonify({"status": "not-ready"}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
