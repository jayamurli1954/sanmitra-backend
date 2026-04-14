"""Shared SlowAPI rate limiter instance for public endpoints."""
from slowapi import Limiter
from slowapi.util import get_remote_address

# Keyed by client IP; can be overridden to key by authenticated user for admin routes
limiter = Limiter(key_func=get_remote_address)
