"""
Circuit Breakers and Retry Logic
Prevents cascading failures when external services (OpenAI, Qdrant) fail
"""
import logging
from functools import wraps
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
    after_log
)
from openai import RateLimitError, APIConnectionError, APITimeoutError

logger = logging.getLogger(__name__)


# ============================================================================
# OPENAI CIRCUIT BREAKER
# ============================================================================

def with_openai_retry(func):
    """
    Decorator for OpenAI API calls with exponential backoff retry.
    
    Retries on:
    - Rate limit errors (429)
    - Connection errors
    - Timeout errors
    
    Strategy:
    - Max 3 attempts
    - Exponential backoff: 2s, 4s, 8s
    - Logs before each retry
    """
    @retry(
        retry=retry_if_exception_type((
            RateLimitError,
            APIConnectionError,
            APITimeoutError
        )),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        after=after_log(logger, logging.INFO)
    )
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except RateLimitError as e:
            logger.warning(f"OpenAI rate limit hit, retrying... {e}")
            raise
        except APIConnectionError as e:
            logger.warning(f"OpenAI connection error, retrying... {e}")
            raise
        except APITimeoutError as e:
            logger.warning(f"OpenAI timeout, retrying... {e}")
            raise
    
    @retry(
        retry=retry_if_exception_type((
            RateLimitError,
            APIConnectionError,
            APITimeoutError
        )),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        after=after_log(logger, logging.INFO)
    )
    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except RateLimitError as e:
            logger.warning(f"OpenAI rate limit hit, retrying... {e}")
            raise
        except APIConnectionError as e:
            logger.warning(f"OpenAI connection error, retrying... {e}")
            raise
        except APITimeoutError as e:
            logger.warning(f"OpenAI timeout, retrying... {e}")
            raise
    
    # Return appropriate wrapper based on function type
    import inspect
    if inspect.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper


# ============================================================================
# QDRANT CIRCUIT BREAKER
# ============================================================================

def with_qdrant_retry(func):
    """
    Decorator for Qdrant operations with retry logic.
    
    Retries on:
    - Connection errors
    - Timeout errors
    
    Strategy:
    - Max 3 attempts
    - Exponential backoff: 1s, 2s, 4s
    """
    @retry(
        retry=retry_if_exception_type(Exception),  # Catch Qdrant exceptions
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            # Log but allow retry mechanism to handle
            if "connection" in str(e).lower() or "timeout" in str(e).lower():
                logger.warning(f"Qdrant transient error, retrying... {e}")
            raise
    
    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )
    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "connection" in str(e).lower() or "timeout" in str(e).lower():
                logger.warning(f"Qdrant transient error, retrying... {e}")
            raise
    
    # Return appropriate wrapper based on function type
    import inspect
    if inspect.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper


# ============================================================================
# GENERIC CIRCUIT BREAKER
# ============================================================================

def with_retry(max_attempts=3, min_wait=1, max_wait=10):
    """
    Generic retry decorator for any function.
    
    Usage:
        @with_retry(max_attempts=3, min_wait=2, max_wait=8)
        async def my_api_call():
            ...
    """
    def decorator(func):
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True
        )
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True
        )
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        
        import inspect
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator

