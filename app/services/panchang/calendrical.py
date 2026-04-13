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
    """Calculate dynamic Hindu calendar information with proper Samvatsara alignment"""
    # Shaka year calculation: Year 0 of Shaka era was 78 CE
    # Shaka year changes on Chaitra Shukla Prathama (around March 21-April 14 CE)
    shaka_year = dt.year - 78
    if dt.month < 3 or (dt.month == 3 and dt.day < 21):
        shaka_year -= 1

    # Vikram Samvat = Shaka year + 135
    vikram_samvat = shaka_year + 135

    # Samvatsara (60-year cycle) calculation
    # Base calibration: Shaka 1909 (1987 CE) = Prabhava (index 0)
    # This is the standard Drik Panchang alignment
    base_shaka_year = 1909
    base_samvatsara_index = 0

    # Calculate Shaka Samvatsara index
    shaka_idx = (shaka_year - base_shaka_year + base_samvatsara_index) % 60
    shaka_name = SAMVATSARAS[shaka_idx]

    # Vikram Samvatsara: Vikram year is 135 years ahead of Shaka
    # Vikram Samvatsara advances at the same rate but with different alignment
    # Base: Vikram 1948 (2082 CE) = Kalayukta; therefore Vikram 1949 = Siddharthi, etc.
    # For proper alignment: use Shaka index but account for Vikram offset
    vikram_idx = shaka_idx
    vikram_name = SAMVATSARAS[vikram_idx]

    # Solar position (for solar month/ritu)
    sun_long = get_sidereal_position(jd, swe.SUN)
    solar_month_idx = int(sun_long / 30)
    solar_month = RASHIS[solar_month_idx]

    # Lunar month calculation
    moon_long = get_sidereal_position(jd, swe.MOON)
    diff = (moon_long - sun_long) % 360

    # Lunar month index based on sun's position in zodiac
    # Sun enters each zodiac sign ~30 days apart
    lunar_month_index = int(sun_long / 30)

    # Purnimanta: months named by full moon in that month
    # Chaitra (Chaitra Purnima), Vaishakha (Vaishakha Purnima), etc.
    # Starts from Chaitra
    purnimanta_months = [
        "Chaitra", "Vaishakha", "Jyeshtha", "Ashadha", "Shravana", "Bhadrapada",
        "Ashvina", "Kartika", "Margashirsha", "Pausha", "Magha", "Phalguni"
    ]

    # Amanta: months named by new moon in that month (same names, different calendar)
    # Amanta Chaitra ends on Chaitra Krishna Amavasya (one month after Purnimanta Phalguni)
    # So Amanta is offset by -1 (wraps around)
    amanta_months = [
        "Phalguni", "Chaitra", "Vaishakha", "Jyeshtha", "Ashadha", "Shravana",
        "Bhadrapada", "Ashvina", "Kartika", "Margashirsha", "Pausha", "Magha"
    ]

    purnimanta_index = (lunar_month_index + 11) % 12
    amanta_index = (lunar_month_index + 10) % 12  # Offset by one month (different ending point)

    paksha = "Shukla" if diff < 180 else "Krishna"
    ritu_idx = int(solar_month_idx / 2)
    ritu = RUTHUS[ritu_idx % 6]

    return {
        "vikram_samvat": f"{vikram_samvat} {vikram_name}",
        "shaka_samvat": f"{shaka_year} {shaka_name}",
        "shaka_year": shaka_year,
        "samvatsara_name": shaka_name,
        "samvatsara_cycle_year": shaka_idx + 1,
        "vikram_samvatsara_name": vikram_name,
        "vikram_samvatsara_cycle_year": vikram_idx + 1,
        "solar_month": solar_month,
        "lunar_month_purnimanta": purnimanta_months[purnimanta_index],
        "lunar_month_amanta": amanta_months[amanta_index],
        "paksha": paksha,
        "ritu": ritu,
    }
