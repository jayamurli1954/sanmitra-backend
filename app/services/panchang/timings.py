"""
Panchang timings: Rahu Kala, Gulika, Yamaganda, Muhurtas, etc.
"""

from typing import Dict, List
from .utils import time_to_minutes, minutes_to_time
from .constants import DUR_MUHURTA_INDICES, NAKSHATRA_VARJYAM_STARTS, NAKSHATRA_AMRITA_STARTS, NAKSHATRAS

def get_rahu_kala_data(sunrise: str, sunset: str, day_of_week: int) -> Dict:
    sunrise_min = time_to_minutes(sunrise)
    sunset_min = time_to_minutes(sunset)
    day_duration = sunset_min - sunrise_min
    segment = day_duration / 8

    rahu_segments = {0: 7, 1: 1, 2: 6, 3: 4, 4: 5, 5: 3, 6: 2}
    segment_num = rahu_segments.get(day_of_week, 1)
    start_min = sunrise_min + (segment_num * segment)
    end_min = start_min + segment

    return {
        "start": minutes_to_time(start_min),
        "end": minutes_to_time(end_min),
        "duration_minutes": int(segment),
    }

def get_yamaganda_data(sunrise: str, sunset: str, day_of_week: int) -> Dict:
    sunrise_min = time_to_minutes(sunrise)
    sunset_min = time_to_minutes(sunset)
    day_duration = sunset_min - sunrise_min
    segment = day_duration / 8

    yamaganda_segments = {0: 4, 1: 3, 2: 2, 3: 1, 4: 0, 5: 6, 6: 5}
    segment_num = yamaganda_segments.get(day_of_week, 1)
    start_min = sunrise_min + (segment_num * segment)
    end_min = start_min + segment

    return {
        "start": minutes_to_time(start_min),
        "end": minutes_to_time(end_min),
        "duration_minutes": int(segment),
    }

def get_gulika_data(sunrise: str, sunset: str, day_of_week: int) -> Dict:
    sunrise_min = time_to_minutes(sunrise)
    sunset_min = time_to_minutes(sunset)
    day_duration = sunset_min - sunrise_min
    segment = day_duration / 8

    gulika_segments = {0: 6, 1: 5, 2: 4, 3: 3, 4: 2, 5: 1, 6: 0}
    segment_num = gulika_segments.get(day_of_week, 1)
    start_min = sunrise_min + (segment_num * segment)
    end_min = start_min + segment

    return {
        "start": minutes_to_time(start_min),
        "end": minutes_to_time(end_min),
        "duration_minutes": int(segment),
    }

def get_abhijit_muhurat_data(sunrise: str, sunset: str) -> Dict:
    sunrise_min = time_to_minutes(sunrise)
    sunset_min = time_to_minutes(sunset)
    # Abhijit Muhurta: centered around local midday
    # Standard definition: 24 minutes before midday to 24 minutes after midday
    # Duration: 48 minutes (1 Muhurta)
    # This is the most commonly used and reliable method (matches Drik Panchang)
    midday = (sunrise_min + sunset_min) / 2
    start_min = midday - 24
    end_min = midday + 24
    return {
        "start": minutes_to_time(start_min),
        "end": minutes_to_time(end_min),
        "duration_minutes": 48,
        "description": "Most auspicious period of the day (Abhijit Muhurta - 48 minutes centered on midday)",
    }

def get_brahma_muhurat_data(sunrise: str) -> Dict:
    sunrise_min = time_to_minutes(sunrise)
    # Brahma Muhurta: Exactly 2 Muhurtas (96 minutes) before sunrise
    # Starts at sunrise - 96 minutes, ends at sunrise - 48 minutes
    # Duration: 48 minutes (1 Muhurta)
    # This is the standard definition used by Drik Panchang and most authoritative sources
    start_min = sunrise_min - 96
    end_min = sunrise_min - 48
    return {
        "start": minutes_to_time(start_min),
        "end": minutes_to_time(end_min),
        "duration_minutes": 48,
        "description": "Most auspicious time for meditation, prayer, and spiritual practices (2 muhurtas before sunrise)",
    }

def get_dur_muhurta_data(sunrise: str, sunset: str, day_of_week: int) -> List[Dict]:
    sunrise_min = time_to_minutes(sunrise)
    sunset_min = time_to_minutes(sunset)
    day_duration = sunset_min - sunrise_min
    muhurta_duration = day_duration / 15
    indices = DUR_MUHURTA_INDICES.get(day_of_week, [])
    
    return [
        {
            "start": minutes_to_time(sunrise_min + (idx * muhurta_duration)),
            "end": minutes_to_time(sunrise_min + ((idx + 1) * muhurta_duration)),
            "duration_minutes": int(muhurta_duration),
            "description": "Inauspicious period (Dur Muhurta)",
        } for idx in indices
    ]

def get_varjyam_impl_data(sunrise: str, sunset: str, nakshatra_data: Dict, is_amrita: bool = False) -> List[Dict]:
    """Shared implementation for Varjyam and Amrita Kalam using Nakshatra-based Ghati scaling.

    Falls back to day-duration based calculation if Nakshatra boundaries aren't available.
    """
    from datetime import datetime, timedelta

    nak_name = nakshatra_data.get("name")
    if not nak_name: return []

    try:
        simple_name = nak_name.split(" Pada")[0].strip()
        nak_index = NAKSHATRAS.index(simple_name)
    except:
        return []

    start_ghati = 0
    if is_amrita:
        if 0 <= nak_index < len(NAKSHATRA_AMRITA_STARTS):
            start_ghati = NAKSHATRA_AMRITA_STARTS[nak_index]
    else:
        if 0 <= nak_index < len(NAKSHATRA_VARJYAM_STARTS):
            start_ghati = NAKSHATRA_VARJYAM_STARTS[nak_index]

    if start_ghati == 0:
        # No offset for this nakshatra, cannot calculate
        return []

    start_time_str = nakshatra_data.get("start_time")
    end_time_str = nakshatra_data.get("end_time")

    # If nakshatra boundaries unavailable, calculate from sunrise/sunset
    if not start_time_str or not end_time_str:
        return _calculate_varjyam_from_day(sunrise, sunset, start_ghati, is_amrita)

    try:
        start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
        nakshatra_seconds = (end_dt - start_dt).total_seconds()
    except (ValueError, TypeError):
        # Fallback if datetime parsing fails
        return _calculate_varjyam_from_day(sunrise, sunset, start_ghati, is_amrita)

    if nakshatra_seconds <= 0:
        # Invalid nakshatra span, fallback
        return _calculate_varjyam_from_day(sunrise, sunset, start_ghati, is_amrita)

    # Standard Panchang rule:
    # Divide actual Nakshatra span into 60 Ghatis and apply offset proportionally.
    # Amrita is 4 ghatis (48 min worth), Varjyam is 4 ghatis.
    one_ghati_seconds = nakshatra_seconds / 60.0

    # Clamp ghati offset to valid range (0-60) to avoid extending beyond nakshatra
    effective_ghati = min(start_ghati, 56)  # Leave room for 4-ghati duration

    event_start_dt = start_dt + timedelta(seconds=(effective_ghati * one_ghati_seconds))
    event_end_dt = event_start_dt + timedelta(seconds=(4 * one_ghati_seconds))

    # Ensure times stay within sensible day bounds (sunrise to sunset)
    # Convert sunrise/sunset strings to comparable times
    try:
        sunrise_dt = datetime.strptime(sunrise, "%H:%M:%S").time()
        sunset_dt = datetime.strptime(sunset, "%H:%M:%S").time()
        # Compare times of day
        if event_start_dt.time() > sunset_dt or event_start_dt.time() < sunrise_dt:
            # Event is outside normal daytime, skip it
            return []
    except (ValueError, TypeError):
        pass

    # Also clip end time to not extend past sunset if it does
    try:
        sunset_dt = datetime.strptime(sunset, "%H:%M:%S")
        if event_end_dt.time() > sunset_dt.time():
            # Calculate original duration
            orig_duration_seconds = (event_end_dt - event_start_dt).total_seconds()
            # Clamp end to sunset, keeping at least some duration
            event_end_dt_clamped = start_dt.replace(hour=sunset_dt.hour, minute=sunset_dt.minute, second=sunset_dt.second)
            if event_end_dt_clamped < event_start_dt:
                # Won't work, skip this event
                return []
            event_end_dt = event_end_dt_clamped
    except (ValueError, TypeError):
        pass

    duration_minutes = ((event_end_dt - event_start_dt).total_seconds()) / 60.0

    if duration_minutes <= 0:
        return []

    return [{
        "start": event_start_dt.strftime("%H:%M:%S"),
        "end": event_end_dt.strftime("%H:%M:%S"),
        "start_datetime": event_start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "end_datetime": event_end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_minutes": round(duration_minutes, 2),
        "description": "Amrita Kalam" if is_amrita else "Varjyam",
    }]


def _calculate_varjyam_from_day(sunrise: str, sunset: str, start_ghati: int, is_amrita: bool = False) -> List[Dict]:
    """Fallback: Calculate Varjyam/Amrita from day duration if Nakshatra boundaries unavailable."""
    from datetime import datetime, timedelta

    # Convert sunrise to datetime (assume same date)
    try:
        sunrise_time = datetime.strptime(f"2000-01-01 {sunrise}", "%Y-%m-%d %H:%M:%S")
        sunset_time = datetime.strptime(f"2000-01-01 {sunset}", "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return []

    day_seconds = (sunset_time - sunrise_time).total_seconds()
    if day_seconds <= 0:
        return []

    # Use actual day duration for proportional calculation (more accurate)
    # Divide the actual day span into 60 ghatis for precise timing
    one_ghati_seconds = day_seconds / 60.0

    # Clamp ghati to valid range (0-56 to leave room for 4-ghati duration)
    effective_ghati = min(start_ghati, 56)

    event_start_dt = sunrise_time + timedelta(seconds=(effective_ghati * one_ghati_seconds))
    event_end_dt = event_start_dt + timedelta(seconds=(4 * one_ghati_seconds))

    # Ensure times stay within day bounds [sunrise, sunset]
    if event_start_dt > sunset_time:
        # Event is completely after sunset, skip it
        return []
    if event_start_dt < sunrise_time:
        event_start_dt = sunrise_time
    if event_end_dt > sunset_time:
        event_end_dt = sunset_time

    duration_minutes = ((event_end_dt - event_start_dt).total_seconds()) / 60.0

    # Only return valid events with non-zero duration
    if duration_minutes <= 0:
        return []

    return [{
        "start": event_start_dt.strftime("%H:%M:%S"),
        "end": event_end_dt.strftime("%H:%M:%S"),
        "start_datetime": event_start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "end_datetime": event_end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_minutes": round(duration_minutes, 2),
        "description": "Amrita Kalam (fallback)" if is_amrita else "Varjyam (fallback)",
    }]
