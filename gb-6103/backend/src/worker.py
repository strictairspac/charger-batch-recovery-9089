import logging
import time

from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.config.resources import cache, engine
from src.services.execution import JobFailure, fail, service_one

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")


def check():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    heartbeat = cache.get("workbench:heartbeat")
    return heartbeat is not None and time.time() - float(heartbeat) < 90


def work():
    while True:
        try:
            cache.set("workbench:heartbeat", time.time(), ex=90)
            try:
                if service_one():
                    continue
            except JobFailure as exc:
                outcome = fail(exc)
                if outcome == "failed":
                    logger.error("Execution %s failed permanently: %s", exc.run_id, exc.cause)
                elif outcome == "retry":
                    logger.warning("Execution %s interrupted; queued to resume: %s", exc.run_id, exc.cause)
                    time.sleep(1)
                # A stale attempt belongs to an older claim; drop it silently.
                continue
            cache.blpop("workbench:wake", timeout=1)
        except (RedisError, SQLAlchemyError):
            logger.exception("Worker connection unavailable")
            time.sleep(2)


if __name__ == "__main__":
    work()
