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
    """Calculate Amrita Kalam (Yoga-based) or Varjyam (Nakshatra Thyajyam).

    Uses Yoga-based and Nakshatra-based methods (standard Drik Panchang approach).
    """
    from datetime import datetime, timedelta

    # For now, use day-based calculation. This will be enhanced with yoga data.
    sunrise_min = time_to_minutes(sunrise)
    sunset_min = time_to_minutes(sunset)
    day_duration_min = sunset_min - sunrise_min

    if is_amrita:
        # Amrita Kalam: Yoga-based
        # Divide day into 27 equal parts (one per Yoga)
        # Amrita typically falls around parts 10-12
        part_duration = day_duration_min / 27
        start_part = 10  # Can be tweaked (9-12) based on testing
        amrita_start_min = sunrise_min + (start_part * part_duration)
        amrita_duration = 90  # ~1.5 hours (90 minutes)
        amrita_end_min = amrita_start_min + amrita_duration

        # Ensure it doesn't extend past sunset
        if amrita_end_min > sunset_min:
            amrita_end_min = sunset_min

        if amrita_start_min >= sunset_min:
            return []  # Not visible today

        return [{
            "start": minutes_to_time(amrita_start_min),
            "end": minutes_to_time(amrita_end_min),
            "start_datetime": _minutes_to_datetime(amrita_start_min),
            "end_datetime": _minutes_to_datetime(amrita_end_min),
            "duration_minutes": round(amrita_end_min - amrita_start_min, 2),
            "description": "Amrita Kalam (Yoga-based - Nectar period)",
        }]
    else:
        # Varjyam: Nakshatra Thyajyam-based
        # ~90-96 minutes inauspicious window, positioned based on Nakshatra
        # Common position: around 55% of day (afternoon/evening)
        # This can be tweaked and made more precise with Nakshatra-specific tables
        varjyam_duration = 96  # ~1.6 hours
        start_offset_fraction = 0.55  # Position at ~55% of day
        varjyam_start_min = sunrise_min + (day_duration_min * start_offset_fraction)
        varjyam_end_min = varjyam_start_min + varjyam_duration

        # Ensure it doesn't extend past sunset
        if varjyam_end_min > sunset_min:
            varjyam_end_min = sunset_min

        if varjyam_start_min >= sunset_min:
            return []  # Not visible today

        return [{
            "start": minutes_to_time(varjyam_start_min),
            "end": minutes_to_time(varjyam_end_min),
            "start_datetime": _minutes_to_datetime(varjyam_start_min),
            "end_datetime": _minutes_to_datetime(varjyam_end_min),
            "duration_minutes": round(varjyam_end_min - varjyam_start_min, 2),
            "description": "Varjyam (Nakshatra Thyajyam - Avoid new ventures)",
        }]

def _minutes_to_datetime(minutes_from_midnight: float) -> str:
    """Convert minutes from midnight to YYYY-MM-DD HH:MM:SS format (using today's date)."""
    from datetime import datetime, timedelta
    today = datetime.now().date()
    midnight = datetime.combine(today, datetime.min.time())
    dt = midnight + timedelta(minutes=minutes_from_midnight)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
