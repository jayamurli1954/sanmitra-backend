"""
Shared utilities for Panchang calculations
"""

def time_to_minutes(time_str: str) -> int:
    """Convert HH:MM:SS to minutes from midnight"""
    parts = time_str.split(":")
    return int(parts[0]) * 60 + int(parts[1])

def minutes_to_time(minutes: float) -> str:
    """Convert minutes from midnight to HH:MM:00"""
    minutes = minutes % 1440
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    return f"{hours:02d}:{mins:02d}:00"
