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
    midday = (sunrise_min + sunset_min) / 2
    day_duration = sunset_min - sunrise_min
    duration = day_duration / 15
    return {
        "start": minutes_to_time(midday - (duration / 2)),
        "end": minutes_to_time(midday + (duration / 2)),
        "duration_minutes": duration,
    }

def get_brahma_muhurat_data(sunrise: str) -> Dict:
    sunrise_min = time_to_minutes(sunrise)
    # Traditional definition: starts ~96 minutes before sunrise (2 muhurtas of 48 min total window here)
    start_min = sunrise_min - 96
    return {
        "start": minutes_to_time(start_min),
        "end": minutes_to_time(start_min + 48),
        "duration_minutes": 48,
        "description": "Most auspicious time for meditation, prayer, and spiritual practices",
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
    """Shared implementation for Varjyam and Amrita Kalam using dynamic Ghati scaling."""
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

    if start_ghati == 0: return []

    start_time_str = nakshatra_data.get("start_time")
    end_time_str = nakshatra_data.get("end_time")
    if not start_time_str or not end_time_str: return []

    start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
    end_dt = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
    nakshatra_seconds = (end_dt - start_dt).total_seconds()
    if nakshatra_seconds <= 0:
        return []

    # Standard Panchang rule:
    # divide actual Nakshatra span into 60 Ghatis and apply offset proportionally.
    one_ghati_seconds = nakshatra_seconds / 60.0
    event_start_dt = start_dt + timedelta(seconds=(start_ghati * one_ghati_seconds))
    event_end_dt = event_start_dt + timedelta(seconds=(4 * one_ghati_seconds))
    duration_minutes = ((event_end_dt - event_start_dt).total_seconds()) / 60.0

    return [{
        "start": event_start_dt.strftime("%H:%M:%S"),
        "end": event_end_dt.strftime("%H:%M:%S"),
        "start_datetime": event_start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "end_datetime": event_end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_minutes": round(duration_minutes, 2),
        "description": "Amrita Kalam" if is_amrita else "Varjyam",
    }]
