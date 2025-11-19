"""
Dramatiq Redis Broker Configuration
Handles background job queue for large sync operations
"""
import os
import logging
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import (
    AgeLimit, Callbacks, Pipelines,
    Retries, ShutdownNotifications
)

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL")

if not REDIS_URL:
    logger.warning("⚠️  REDIS_URL not set - background jobs will not work")
    redis_broker = RedisBroker()
else:
    # Create broker with explicit middleware (excludes TimeLimit for Python 3.13 compatibility)
    redis_broker = RedisBroker(
        url=REDIS_URL,
        middleware=[
            AgeLimit(),
            Retries(max_retries=3),
            Callbacks(),
            Pipelines(),
            ShutdownNotifications(),
            # TimeLimit intentionally excluded - Python 3.13 incompatibility
        ]
    )
    logger.info(f"✅ Redis broker initialized: {REDIS_URL[:20]}...")

dramatiq.set_broker(redis_broker)
broker = redis_broker

