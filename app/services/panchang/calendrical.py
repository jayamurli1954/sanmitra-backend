"""
Calendrical calculations: Ayana, Ruthu, Samvatsara, Hindu Calendar Info
"""

import swisseph as swe
from datetime import datetime
from typing import Dict
from .astro_utils import get_sidereal_position
from .constants import RASHIS, RUTHUS, SAMVATSARAS

def get_moon_sign_data(jd: float) -> Dict:
    """Calculate Moon's Rashi"""
    moon_long = get_sidereal_position(jd, swe.MOON)
    rashi_num = int(moon_long / 30)

    return {
        "number": rashi_num + 1,
        "name": RASHIS[rashi_num],
        "moon_longitude": round(moon_long, 2),
    }

def get_ayana_data(jd: float) -> str:
    """Calculate Ayana"""
    sun_long = get_sidereal_position(jd, swe.SUN)
    if 270 <= sun_long or sun_long < 90:
        return "Uttarayana"
    else:
        return "Dakshinayana"

def get_ruthu_data(jd: float) -> str:
    """Calculate Ruthu"""
    sun_long = get_sidereal_position(jd, swe.SUN)
    if 330 <= sun_long or sun_long < 30:
        return "Vasanta"
    elif 30 <= sun_long < 90:
        return "Grishma"
    elif 90 <= sun_long < 150:
        return "Varsha"
    elif 150 <= sun_long < 210:
        return "Sharad"
    elif 210 <= sun_long < 270:
        return "Hemanta"
    else:
        return "Shishira"

def get_samvatsara_data(year: int) -> Dict:
    """Calculate Samvatsara name"""
    shaka_year = year - 78
    samvatsara_index = (shaka_year + 11) % 60

    return {
        "number": samvatsara_index + 1,
        "name": SAMVATSARAS[samvatsara_index],
        "shaka_year": shaka_year,
        "kali_year": year + 3102,
        "cycle_year": samvatsara_index + 1,
    }

def get_hindu_calendar_info_data(dt: datetime, jd: float) -> Dict:
    """Calculate dynamic Hindu calendar information"""
    shaka_year = dt.year - 78
    if dt.month < 4 or (dt.month == 4 and dt.day < 14):
        shaka_year -= 1
    vikram_samvat = shaka_year + 135

    # Samvatsara cycle base alignment:
    # 1987 CE (Shaka 1909) -> Prabhava (index 0)
    shaka_idx = (shaka_year + 11) % 60
    shaka_name = SAMVATSARAS[shaka_idx]

    # Vikram year-name cycle alignment:
    # 2082 -> Kalayukta (as per temple validation baseline)
    vikram_idx = (vikram_samvat + 9) % 60
    vikram_name = SAMVATSARAS[vikram_idx]

    sun_long = get_sidereal_position(jd, swe.SUN)
    solar_month_idx = int(sun_long / 30)
    solar_month = RASHIS[solar_month_idx]

    moon_long = get_sidereal_position(jd, swe.MOON)
    diff = (moon_long - sun_long) % 360
    lunar_month_index = int(sun_long / 30)

    purnimanta_months = [
        "Chaitra", "Vaishakha", "Jyeshtha", "Ashadha", "Shravana", "Bhadrapada",
        "Ashvina", "Kartika", "Margashirsha", "Pausha", "Magha", "Phalguni"
    ]
    
    purnimanta_index = (lunar_month_index + 11) % 12
    amanta_index = (lunar_month_index + 11) % 12

    paksha = "Shukla" if diff < 180 else "Krishna"
    ritu_idx = int(solar_month_idx / 2)
    ritu = RUTHUS[ritu_idx % 6]

    return {
        "vikram_samvat": f"{vikram_samvat} {vikram_name}",
        "shaka_samvat": f"{shaka_year} {shaka_name}",
        "shaka_year": shaka_year,
        "samvatsara_name": shaka_name,
        "samvatsara_cycle_year": shaka_idx + 1,
        "solar_month": solar_month,
        "lunar_month_purnimanta": purnimanta_months[purnimanta_index],
        "lunar_month_amanta": purnimanta_months[amanta_index],
        "paksha": paksha,
        "ritu": ritu,
    }
