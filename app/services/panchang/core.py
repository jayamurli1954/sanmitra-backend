"""
Core Panchang calculations: Tithi, Nakshatra, Yoga, Karana
"""

import swisseph as swe
from typing import Dict
from .astro_utils import get_sidereal_position, jd_to_datetime
from .constants import NAKSHATRAS, TITHIS, YOGAS, KARANAS

def get_nakshatra_data(jd: float) -> Dict:
    """Calculate current Nakshatra"""
    moon_long = get_sidereal_position(jd, swe.MOON)

    # Each nakshatra is 13°20' (13.333...)
    nak_num = int(moon_long / 13.333333333333)
    nak_pada = int((moon_long % 13.333333333333) / 3.333333333333) + 1

    def _norm_angle_deg(val: float) -> float:
        return ((val + 180.0) % 360.0) - 180.0

    def _moon_minus_target(test_jd: float, target_deg: float) -> float:
        return _norm_angle_deg(get_sidereal_position(test_jd, swe.MOON) - target_deg)

    def _find_crossing(jd_seed: float, target_deg: float, forward: bool) -> float:
        """
        Find boundary crossing for Moon longitude = target_deg.
        Uses sign-change bracketing + binary search for stability.
        """
        step = 0.02  # ~29 minutes
        prev_jd = jd_seed
        prev_val = _moon_minus_target(prev_jd, target_deg)

        # Fast path near exact boundary
        if abs(prev_val) < 1e-7:
            return prev_jd

        lo = hi = None
        for _ in range(400):  # search window ~8 days max
            curr_jd = prev_jd + step if forward else prev_jd - step
            curr_val = _moon_minus_target(curr_jd, target_deg)

            if forward:
                if prev_val <= 0 <= curr_val:
                    lo, hi = prev_jd, curr_jd
                    break
            else:
                if curr_val <= 0 <= prev_val:
                    lo, hi = curr_jd, prev_jd
                    break

            prev_jd, prev_val = curr_jd, curr_val

        if lo is None or hi is None:
            return jd_seed

        for _ in range(60):
            mid = (lo + hi) / 2.0
            mid_val = _moon_minus_target(mid, target_deg)
            if mid_val < 0:
                lo = mid
            else:
                hi = mid

        return (lo + hi) / 2.0

    # Current Nakshatra start/end boundaries.
    target_start = (nak_num * 13.333333333333) % 360
    target_end = ((nak_num + 1) * 13.333333333333) % 360
    jd_start_val = _find_crossing(jd, target_start, forward=False)
    jd_end = _find_crossing(jd, target_end, forward=True)

    start_time_dt = jd_to_datetime(jd_start_val) if jd_start_val else None
    end_time_dt = jd_to_datetime(jd_end) if jd_end else None

    return {
        "number": nak_num + 1,
        "name": NAKSHATRAS[nak_num % 27],
        "pada": nak_pada,
        "moon_longitude": round(moon_long, 2),
        "start_time": start_time_dt.strftime("%Y-%m-%d %H:%M:%S") if start_time_dt else None,
        "end_time": end_time_dt.strftime("%Y-%m-%d %H:%M:%S") if end_time_dt else None,
        "end_time_formatted": end_time_dt.strftime("%I:%M %p") if end_time_dt else None,
        "next_nakshatra": NAKSHATRAS[(nak_num + 1) % 27],
    }

def get_tithi_data(jd: float) -> Dict:
    """Calculate current Tithi with accurate Paksha and special handling.

    Bug fix: Corrected Paksha logic to handle Amavasya/Purnima boundary correctly.
    """
    moon_long = get_sidereal_position(jd, swe.MOON)
    sun_long = get_sidereal_position(jd, swe.SUN)

    diff = (moon_long - sun_long + 360) % 360
    tithi_index = int(diff / 12)                    # 0 to 29
    tithi_num = (tithi_index % 15) + 1               # 1 to 15

    # Correct Paksha logic (critical bug fix)
    if tithi_index < 15:
        paksha = "Shukla"
        tithi_name = TITHIS[tithi_num - 1]
    else:
        paksha = "Krishna"
        tithi_name = TITHIS[tithi_num - 1]

    # Special names for 15th tithi (Purnima/Amavasya)
    if tithi_num == 15 and paksha == "Shukla":
        tithi_name = "Purnima"
    elif tithi_num == 15 and paksha == "Krishna":
        tithi_name = "Amavasya"

    # Find exact end time using binary search
    target_diff = ((tithi_index + 1) * 12) % 360

    def get_tithi_diff(test_jd):
        m = get_sidereal_position(test_jd, swe.MOON)
        s = get_sidereal_position(test_jd, swe.SUN)
        return (m - s + 360) % 360

    from .astro_utils import find_transition
    jd_end = find_transition(jd, target_diff, get_tithi_diff)
    end_dt = jd_to_datetime(jd_end)

    return {
        "number": tithi_num,
        "name": tithi_name,
        "paksha": paksha,
        "full_name": f"{paksha} {tithi_name}",
        "is_special": tithi_name in ["Ekadashi", "Purnima", "Amavasya"],
        "elongation": round(diff, 2),
        "end_time": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time_formatted": end_dt.strftime("%I:%M %p"),
        "ends_at_ist": end_dt.strftime("%I:%M %p"),
        "ends_at_jd": jd_end,
    }

def get_yoga_data(jd: float) -> Dict:
    """Calculate current Yoga"""
    moon_long = get_sidereal_position(jd, swe.MOON)
    sun_long = get_sidereal_position(jd, swe.SUN)

    total = (moon_long + sun_long) % 360
    yoga_num = int(total / 13.333333)

    target_total = ((yoga_num + 1) * 13.333333333333) % 360
    
    def get_yoga_diff(jd):
        moon = get_sidereal_position(jd, swe.MOON)
        sun = get_sidereal_position(jd, swe.SUN)
        return (moon + sun) % 360

    from .astro_utils import find_transition
    jd_end = find_transition(jd, target_total, get_yoga_diff)
    end_time_dt = jd_to_datetime(jd_end)

    return {
        "number": yoga_num + 1,
        "name": YOGAS[yoga_num],
        "is_inauspicious": YOGAS[yoga_num] in ["Vyatipata", "Vaidhriti"],
        "end_time": end_time_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time_formatted": end_time_dt.strftime("%I:%M %p"),
    }

def get_karana_data(jd: float) -> Dict:
    """Calculate current Karana"""
    moon_long = get_sidereal_position(jd, swe.MOON)
    sun_long = get_sidereal_position(jd, swe.SUN)

    diff = (moon_long - sun_long + 360) % 360
    karana_full_index = int(diff / 6)

    target_diff = ((karana_full_index + 1) * 6) % 360
    
    def get_karana_diff(jd):
        moon = get_sidereal_position(jd, swe.MOON)
        sun = get_sidereal_position(jd, swe.SUN)
        return (moon - sun + 360) % 360

    from .astro_utils import find_transition
    jd_end = find_transition(jd, target_diff, get_karana_diff)
    end_time_dt = jd_to_datetime(jd_end)

    karanas = []
    for i in range(2):
        k_num = karana_full_index + i
        if k_num == 0:
            name = "Kimstughna"
        elif k_num >= 57:
            fixed_index = k_num - 57
            name = KARANAS[min(7 + fixed_index, 10)]
        else:
            name = KARANAS[(k_num - 1) % 7]

        karanas.append({
            "name": name,
            "half": "First" if i == 0 else "Second",
            "is_bhadra": name == "Vishti",
        })

    return {
        "current": karanas[0]["name"],
        "first_half": karanas[0],
        "second_half": karanas[1],
        "is_bhadra": karanas[0]["is_bhadra"] or karanas[1]["is_bhadra"],
        "end_time_formatted": end_time_dt.strftime("%I:%M %p"),
    }
