"""
Celestial events: Sunrise, Sunset, Moonrise, Moonset
"""

import swisseph as swe
from datetime import datetime, timedelta
from typing import Dict
from .astro_utils import get_julian_day, jd_to_datetime

def get_sun_rise_set_data(dt: datetime, lat: float, lon: float) -> Dict:
    """Calculate Sunrise & Sunset"""
    swe.set_topo(lon, lat, 920.0)
    geopos = [lon, lat, 920.0]
    jd = swe.julday(dt.year, dt.month, dt.day, 12.0)

    res_rise = swe.rise_trans(jd, swe.SUN, swe.CALC_RISE | swe.BIT_DISC_CENTER, geopos, 0, 0)
    sunrise_jd = res_rise[1][0] if res_rise[0] == 0 else None

    res_set = swe.rise_trans(jd, swe.SUN, swe.CALC_SET | swe.BIT_DISC_CENTER, geopos, 0, 0)
    sunset_jd = res_set[1][0] if res_set[0] == 0 else None

    def jd_to_time_string(jd_value):
        if jd_value is None: return "N/A"
        year, month, day, hour_utc = swe.revjul(jd_value)
        dt_utc = datetime(year, month, day, 0, 0, 0) + timedelta(hours=hour_utc)
        dt_ist = dt_utc + timedelta(hours=5, minutes=30)
        return dt_ist.strftime("%H:%M:%S")

    return {
        "sunrise": jd_to_time_string(sunrise_jd),
        "sunset": jd_to_time_string(sunset_jd),
    }

def get_moon_rise_set_data(dt: datetime, lat: float, lon: float) -> Dict:
    """Calculate Moonrise & Moonset"""
    swe.set_topo(lon, lat, 920.0)
    geopos = [lon, lat, 920.0]
    dt_midnight = datetime(dt.year, dt.month, dt.day)
    jd_start = get_julian_day(dt_midnight)

    res_rise = swe.rise_trans(jd_start, swe.MOON, swe.CALC_RISE | swe.BIT_DISC_CENTER, geopos, 0, 0)
    rise_jd = res_rise[1][0] if res_rise[0] == 0 else None

    set_jd = None
    if rise_jd:
        res_set = swe.rise_trans(rise_jd, swe.MOON, swe.CALC_SET | swe.BIT_DISC_CENTER, geopos, 0, 0)
        if res_set[0] == 0: set_jd = res_set[1][0]
    else:
        res_set = swe.rise_trans(jd_start, swe.MOON, swe.CALC_SET | swe.BIT_DISC_CENTER, geopos, 0, 0)
        if res_set[0] == 0: set_jd = res_set[1][0]

    def fmt(jd_val):
        if jd_val is None or jd_val <= 0: return "N/A"
        dt_event = jd_to_datetime(jd_val)
        time_str = dt_event.strftime("%I:%M %p")
        if time_str.startswith("0"): time_str = time_str[1:]
        if dt_event.date() > dt.date(): time_str += "+"
        elif dt_event.date() < dt.date(): time_str += "-"
        return time_str

    return {"moonrise": fmt(rise_jd), "moonset": fmt(set_jd)}
