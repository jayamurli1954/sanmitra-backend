"""
Panchang timings: Rahu Kala, Gulika, Yamaganda, Muhurtas, etc.
"""

from typing import Dict, List
from .utils import time_to_minutes, minutes_to_time
from .constants import (
    DUR_MUHURTA_INDICES,
    NAKSHATRA_VARJYAM_STARTS,
    NAKSHATRA_AMRITA_STARTS,
    NAKSHATRA_THYAJYAM,
    NAKSHATRAS
)

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
    """Brahma Muhurta: Standard 96 minutes before sunrise (2 muhurtas).

    This is the most auspicious time for meditation and spiritual practices.
    Duration: 48 minutes (1 muhurta).
    Based on Drik Panchang and authoritative Vedic sources.
    """
    sunrise_min = time_to_minutes(sunrise)
    # Standard: 2 Muhurtas (96 minutes) before sunrise
    start_min = sunrise_min - 96
    end_min = sunrise_min - 48

    return {
        "start": minutes_to_time(start_min),
        "end": minutes_to_time(end_min),
        "duration_minutes": 48,
        "description": "Pre-dawn meditation • Most auspicious for spiritual practices",
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
    """Calculate Varjyam (Nakshatra Thyajyam) or Amrita Kalam based on nakshatra boundaries.

    CRITICAL FIX: Varjyam occurs in evening/night AFTER sunset (e.g., 10:18 PM - 11:51 PM)
    - Does NOT clamp to sunset like Amrita Kalam
    - Uses actual nakshatra duration + Thyajyam ghati offsets
    - Properly handles night-time windows that may cross midnight
    """
    from datetime import datetime, timedelta

    sunrise_min = time_to_minutes(sunrise)
    sunset_min = time_to_minutes(sunset)
    day_duration_min = sunset_min - sunrise_min

    if is_amrita:
        # Amrita Kalam: Yoga-based, daytime-only
        # Divide day into 27 equal parts (one per Yoga)
        # Amrita Kalam usually falls in the 10th to 12th part of the day
        part_duration = day_duration_min / 27.0
        start_part = 10  # Standard position (can adjust 9-12)
        amrita_start_min = sunrise_min + (start_part * part_duration)
        amrita_duration = 90  # Standard ~1.5 hours (90 minutes)
        amrita_end_min = amrita_start_min + amrita_duration

        # Safety: don't cross sunset
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
            "description": "Amrita Kalam (Yoga-based) • Nectar period • Highly auspicious",
        }]
    else:
        # Varjyam: Nakshatra Thyajyam Table (occurs in evening/night after sunset)
        # Uses authoritative 27-Nakshatra Thyajyam table (Drik standard)
        # Key insight: Each nakshatra has a specific inauspicious ghati window within its 60-ghati duration

        # Extract nakshatra information
        nak_name = nakshatra_data.get("name", "")
        try:
            # Handle "Nakshatra Pada N" format
            simple_name = nak_name.split(" Pada")[0].strip() if " Pada" in nak_name else nak_name
            nak_index = NAKSHATRAS.index(simple_name)
        except (ValueError, IndexError):
            return []  # Cannot determine nakshatra

        # Lookup Thyajyam ghati range for this nakshatra
        if nak_index not in NAKSHATRA_THYAJYAM:
            return []

        start_ghati, end_ghati = NAKSHATRA_THYAJYAM[nak_index]

        # Get nakshatra boundaries (start_time and end_time from core.py's get_nakshatra_data())
        start_time_str = nakshatra_data.get("start_time")
        end_time_str = nakshatra_data.get("end_time")

        # Initialize variables
        varjyam_start_min = varjyam_end_min = None
        varjyam_start_datetime = varjyam_end_datetime = None

        if not start_time_str or not end_time_str:
            # Fallback: use day-relative ghati conversion (1 Ghati = 24 minutes)
            varjyam_start_min = sunrise_min + (start_ghati * 24)
            varjyam_end_min = sunrise_min + (end_ghati * 24)
            varjyam_start_datetime = _minutes_to_datetime(varjyam_start_min)
            varjyam_end_datetime = _minutes_to_datetime(varjyam_end_min)
        else:
            # Precise calculation: apply ghati offsets within nakshatra's full duration
            try:
                start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
                end_dt = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")

                # Calculate nakshatra duration in seconds
                nak_duration_seconds = (end_dt - start_dt).total_seconds()

                # Handle edge case: nakshatra spans midnight (end_dt < start_dt in clock time)
                if nak_duration_seconds < 0:
                    # Nakshatra extends to next day
                    end_dt = end_dt + timedelta(days=1)
                    nak_duration_seconds = (end_dt - start_dt).total_seconds()

                # Duration of one ghati (1/60th of nakshatra) in seconds
                one_ghati_seconds = nak_duration_seconds / 60.0

                if one_ghati_seconds <= 0:
                    return []  # Invalid nakshatra duration

                # Calculate Varjyam start and end times within nakshatra duration
                varjyam_start_dt = start_dt + timedelta(seconds=(start_ghati * one_ghati_seconds))
                varjyam_end_dt = start_dt + timedelta(seconds=(end_ghati * one_ghati_seconds))

                # Preserve full datetime (handles midnight crossing automatically)
                varjyam_start_datetime = varjyam_start_dt.strftime("%Y-%m-%d %H:%M:%S")
                varjyam_end_datetime = varjyam_end_dt.strftime("%Y-%m-%d %H:%M:%S")

                # Convert to minutes for display/validation (use hour/minute from datetime)
                varjyam_start_min = time_to_minutes(varjyam_start_dt.strftime("%H:%M:%S"))
                varjyam_end_min = time_to_minutes(varjyam_end_dt.strftime("%H:%M:%S"))

            except (ValueError, TypeError, ZeroDivisionError):
                # Fallback to simple ghati-to-minutes conversion
                varjyam_start_min = sunrise_min + (start_ghati * 24)
                varjyam_end_min = sunrise_min + (end_ghati * 24)
                varjyam_start_datetime = _minutes_to_datetime(varjyam_start_min)
                varjyam_end_datetime = _minutes_to_datetime(varjyam_end_min)

        # Validation: ensure we have valid data
        if varjyam_start_datetime is None or varjyam_end_datetime is None:
            return []

        # Validate timing makes sense
        try:
            start_check = datetime.strptime(varjyam_start_datetime, "%Y-%m-%d %H:%M:%S")
            end_check = datetime.strptime(varjyam_end_datetime, "%Y-%m-%d %H:%M:%S")
            if end_check <= start_check:
                return []  # Invalid: end before start
        except ValueError:
            return []

        # Calculate duration properly (handles midnight crossing)
        try:
            start_dt_check = datetime.strptime(varjyam_start_datetime, "%Y-%m-%d %H:%M:%S")
            end_dt_check = datetime.strptime(varjyam_end_datetime, "%Y-%m-%d %H:%M:%S")
            duration_seconds = (end_dt_check - start_dt_check).total_seconds()
            duration_minutes = round(duration_seconds / 60.0, 2)
        except (ValueError, TypeError):
            duration_minutes = round(abs(varjyam_end_min - varjyam_start_min), 2)

        return [{
            "start": minutes_to_time(varjyam_start_min),
            "end": minutes_to_time(varjyam_end_min),
            "start_datetime": varjyam_start_datetime,
            "end_datetime": varjyam_end_datetime,
            "duration_minutes": duration_minutes,
            "description": "Varjyam (Nakshatra Thyajyam) • Avoid starting new ventures",
        }]

def _minutes_to_datetime(minutes_from_midnight: float) -> str:
    """Convert minutes from midnight to YYYY-MM-DD HH:MM:SS format (using today's date)."""
    from datetime import datetime, timedelta
    today = datetime.now().date()
    midnight = datetime.combine(today, datetime.min.time())
    dt = midnight + timedelta(minutes=minutes_from_midnight)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
