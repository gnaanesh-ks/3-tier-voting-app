"""
Cricket vs Football - Vote Processing Worker
Blocks on Redis list (BRPOP), consolidates counts, writes to PostgreSQL.
Designed to run as multiple replicas safely (idempotent upsert via atomic increment).
"""
import os
import sys
import time
import signal
import logging

import redis
import psycopg2
from psycopg2 import pool as pg_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("vote-worker")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
VOTE_QUEUE_KEY = os.getenv("VOTE_QUEUE_KEY", "votes")
BRPOP_TIMEOUT_SECS = int(os.getenv("BRPOP_TIMEOUT_SECS", "5"))

PG_HOST = os.getenv("POSTGRES_HOST", "postgres")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB = os.getenv("POSTGRES_DB", "votingdb")
PG_USER = os.getenv("POSTGRES_USER", "voting_user")
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")

_shutdown = False


def _handle_sigterm(signum, frame):
    global _shutdown
    logger.info("Received signal %s, shutting down gracefully...", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def connect_redis():
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=BRPOP_TIMEOUT_SECS + 2,
        retry_on_timeout=True,
    )


def connect_pg_pool():
    return pg_pool.SimpleConnectionPool(
        minconn=1,
        maxconn=5,
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD,
        connect_timeout=5,
    )


def upsert_vote(conn, choice: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO votes (candidate, vote_count)
            VALUES (%s, 1)
            ON CONFLICT (candidate)
            DO UPDATE SET vote_count = votes.vote_count + 1,
                          updated_at = NOW();
            """,
            (choice,),
        )
    conn.commit()


def main():
    logger.info("Starting worker, connecting to Redis at %s:%s", REDIS_HOST, REDIS_PORT)
    r = connect_redis()

    logger.info("Connecting to PostgreSQL at %s:%s/%s", PG_HOST, PG_PORT, PG_DB)
    pool = connect_pg_pool()

    processed = 0
    while not _shutdown:
        try:
            # BRPOP blocks up to BRPOP_TIMEOUT_SECS waiting for work - cheap on CPU, low latency.
            item = r.brpop(VOTE_QUEUE_KEY, timeout=BRPOP_TIMEOUT_SECS)
            if item is None:
                continue  # timed out, loop again (lets us check _shutdown)

            _, choice = item
            if choice not in ("cricket", "football"):
                logger.warning("Discarding invalid vote payload: %s", choice)
                continue

            conn = pool.getconn()
            try:
                upsert_vote(conn, choice)
                processed += 1
                if processed % 50 == 0:
                    logger.info("Processed %d votes so far", processed)
            except psycopg2.Error as db_err:
                conn.rollback()
                logger.error("DB error, re-queueing vote: %s", db_err)
                r.lpush(VOTE_QUEUE_KEY, choice)  # don't lose the vote
                time.sleep(1)
            finally:
                pool.putconn(conn)

        except redis.exceptions.RedisError as redis_err:
            logger.error("Redis error, retrying in 2s: %s", redis_err)
            time.sleep(2)
        except Exception as exc:  # noqa: BLE001 - top-level guard so the pod doesn't crash-loop
            logger.exception("Unexpected error in worker loop: %s", exc)
            time.sleep(2)

    logger.info("Worker exiting cleanly after processing %d votes total", processed)
    pool.closeall()
    sys.exit(0)


if __name__ == "__main__":
    main()
