import asyncio
import logging
import json
import time
import os
import random
from functools import wraps
from typing import Callable, Any

# Configure standard logger
logger = logging.getLogger(__name__)

def limit_concurrency(limit: int = 5):
    """
    Concurrency Limiter for async functions.
    Ideal for LegalMitra (Tavily/Gemini API calls) and InvestMitra (Angel One SmartAPI).
    Prevents hitting rate limits by throttling concurrent executions.
    """
    sem = asyncio.Semaphore(limit)
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            async with sem:
                return await func(*args, **kwargs)
        return wrapper
    return decorator

def audit_logger(module_name: str = "core"):
    """
    Structured Machine Learning / Audit Logger.
    Mandatory for MitraBooks double-entry tracking and InvestMitra trade logs.
    Captures execution status, elapsed time, and errors in a structured JSON format.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                res = func(*args, **kwargs)
                log_entry = {
                    "module": module_name,
                    "step": func.__name__,
                    "status": "success",
                    "time_ms": round((time.time() - start) * 1000, 2)
                }
                logger.info(json.dumps(log_entry))
                return res
            except Exception as e:
                log_entry = {
                    "module": module_name,
                    "step": func.__name__,
                    "status": "error",
                    "error": str(e),
                    "time_ms": round((time.time() - start) * 1000, 2)
                }
                logger.error(json.dumps(log_entry))
                raise
        return wrapper
    return decorator

def async_audit_logger(module_name: str = "core"):
    """
    Async version of the Structured Machine Learning / Audit Logger.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.time()
            try:
                res = await func(*args, **kwargs)
                log_entry = {
                    "module": module_name,
                    "step": func.__name__,
                    "status": "success",
                    "time_ms": round((time.time() - start) * 1000, 2)
                }
                logger.info(json.dumps(log_entry))
                return res
            except Exception as e:
                log_entry = {
                    "module": module_name,
                    "step": func.__name__,
                    "status": "error",
                    "error": str(e),
                    "time_ms": round((time.time() - start) * 1000, 2)
                }
                logger.error(json.dumps(log_entry))
                raise
        return wrapper
    return decorator

def inject_indicators():
    """
    Feature Injector for InvestMitra stock screening.
    Injects dummy technical indicators (RSI, EMA) to raw dataframes to ensure
    consistent data transformations.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(df, *args, **kwargs):
            try:
                import pandas as pd
                if isinstance(df, pd.DataFrame):
                    df = df.copy() # Prevents Pandas mutation warnings
                    if 'close' in df.columns:
                        df['SMA_14'] = df['close'].rolling(window=14).mean()
            except ImportError:
                pass # Graceful fallback if Pandas is not installed in environment
            return func(df, *args, **kwargs)
        return wrapper
    return decorator

def lock_seed(seed: int = 42):
    """
    Deterministic Seed Setter for test suites.
    Ideal for tests/investmitra/ or tests/legalmitra/ where reproducibility is key.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            random.seed(seed)
            try:
                import numpy as np
                np.random.seed(seed)
            except ImportError:
                pass
            return func(*args, **kwargs)
        return wrapper
    return decorator

def dev_mode_fallback(mock_data_func: Callable = None):
    """
    Dev-Mode Fallback.
    CRITICAL for InvestMitra (prevents live trades in dev, falling back to mock)
    and LegalMitra (fallback to Ollama/mock if Gemini API fails).
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            environment = os.getenv("ENVIRONMENT", "development")
            
            try:
                # Enforce mock in dev for InvestMitra to avoid accidental live trades
                if environment != "production" and hasattr(func, "__module__") and "investmitra" in func.__module__:
                    if mock_data_func:
                        return mock_data_func(*args, **kwargs)
                return func(*args, **kwargs)
            except Exception as e:
                # Catch timeouts/rate limits (e.g., Gemini API in LegalMitra)
                logger.warning(f"External API failed in {func.__name__}, falling back. Error: {str(e)}")
                if mock_data_func:
                    return mock_data_func(*args, **kwargs)
                raise
        return wrapper
    return decorator

def async_dev_mode_fallback(mock_data_func: Callable = None):
    """
    Async Dev-Mode Fallback.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            environment = os.getenv("ENVIRONMENT", "development")
            
            try:
                if environment != "production" and hasattr(func, "__module__") and "investmitra" in func.__module__:
                    if mock_data_func:
                        if asyncio.iscoroutinefunction(mock_data_func):
                            return await mock_data_func(*args, **kwargs)
                        return mock_data_func(*args, **kwargs)
                return await func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Async External API failed in {func.__name__}, falling back. Error: {str(e)}")
                if mock_data_func:
                    if asyncio.iscoroutinefunction(mock_data_func):
                        return await mock_data_func(*args, **kwargs)
                    return mock_data_func(*args, **kwargs)
                raise
        return wrapper
    return decorator
