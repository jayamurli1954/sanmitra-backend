"""
Panchang Service - Modular Implementation
"""

import swisseph as swe
from datetime import datetime, timedelta, date
from typing import Dict, Optional, List

from .constants import *
from .astro_utils import *
from .core import *
from .celestial import *
from .calendrical import *
from .timings import *
from .qualities import *
from .utils import *

class PanchangService:
    """
    Professional Panchang calculation service
    Uses Swiss Ephemeris with Lahiri Ayanamsa for accuracy
    Modularized version
    """

    # Expose constants as class attributes
    NAKSHATRAS = NAKSHATRAS
    TITHIS = TITHIS
    YOGAS = YOGAS
    KARANAS = KARANAS
    RASHIS = RASHIS
    RUTHUS = RUTHUS
    SAMVATSARAS = SAMVATSARAS
    VARAS = VARAS
    VARA_SANSKRIT = VARA_SANSKRIT
    VARA_DEITIES = VARA_DEITIES
    NAKSHATRA_VARJYAM_STARTS = NAKSHATRA_VARJYAM_STARTS
    NAKSHATRA_AMRITA_STARTS = NAKSHATRA_AMRITA_STARTS
    DUR_MUHURTA_INDICES = DUR_MUHURTA_INDICES

    def __init__(self):
        swe.set_sid_mode(swe.SIDM_LAHIRI)

    def get_julian_day(self, dt: datetime) -> float:
        return get_julian_day(dt)

    def get_sidereal_position(self, jd: float, planet: int):
        return get_sidereal_position(jd, planet)

    def jd_to_datetime(self, jd: float) -> datetime:
        return jd_to_datetime(jd)

    def get_nakshatra(self, jd: float) -> Dict:
        return get_nakshatra_data(jd)

    def get_tithi(self, jd: float) -> Dict:
        return get_tithi_data(jd)

    def get_yoga(self, jd: float) -> Dict:
        return get_yoga_data(jd)

    def get_karana(self, jd: float) -> Dict:
        return get_karana_data(jd)

    def get_karana_end_time(self, jd: float) -> Optional[str]:
        k_data = self.get_karana(jd)
        if "Vishti" in k_data.get("current", ""):
            return k_data.get("end_time_formatted")
        return None

    def get_vara(self, dt: datetime) -> Dict:
        vara_num = dt.weekday()
        vara_num = (vara_num + 1) % 7
        return {
            "number": vara_num + 1,
            "name": VARAS[vara_num],
            "sanskrit": VARA_SANSKRIT[vara_num],
            "deity": VARA_DEITIES[vara_num],
        }

    def get_sun_rise_set(self, dt: datetime, lat: float, lon: float) -> Dict:
        return get_sun_rise_set_data(dt, lat, lon)

    def get_moon_rise_set(self, dt: datetime, lat: float, lon: float) -> Dict:
        return get_moon_rise_set_data(dt, lat, lon)

    def get_moon_sign(self, jd: float) -> Dict:
        return get_moon_sign_data(jd)

    def get_ayana(self, jd: float) -> str:
        return get_ayana_data(jd)

    def get_ruthu(self, jd: float) -> str:
        return get_ruthu_data(jd)

    def get_samvatsara(self, year: int) -> Dict:
        return get_samvatsara_data(year)

    def get_hindu_calendar_info(self, dt: datetime, jd: float, tithi_data: Dict) -> Dict:
        return get_hindu_calendar_info_data(dt, jd)

    def get_rahu_kala(self, sunrise: str, sunset: str, day_of_week: int) -> Dict:
        return get_rahu_kala_data(sunrise, sunset, day_of_week)

    def get_yamaganda(self, sunrise: str, sunset: str, day_of_week: int) -> Dict:
        return get_yamaganda_data(sunrise, sunset, day_of_week)

    def get_gulika(self, sunrise: str, sunset: str, day_of_week: int) -> Dict:
        return get_gulika_data(sunrise, sunset, day_of_week)

    def get_nakshatra_quality(self, nakshatra_name: str) -> Dict:
        return get_nakshatra_quality_data(nakshatra_name)

    def get_tithi_quality(self, tithi_name: str) -> Dict:
        return get_tithi_quality_data(tithi_name)

    def get_abhijit_muhurat(self, sunrise: str, sunset: str) -> Dict:
        return get_abhijit_muhurat_data(sunrise, sunset)

    def get_brahma_muhurat(self, sunrise: str) -> Dict:
        return get_brahma_muhurat_data(sunrise)

    def get_amrita_kalam(self, sunrise: str, sunset: str, nakshatra_data: Dict) -> Dict:
        # We need varjyam_impl, I'll add it to timings.py
        from .timings import get_varjyam_impl_data
        res = get_varjyam_impl_data(sunrise, sunset, nakshatra_data, is_amrita=True)
        return res[0] if res else {"start": "N/A", "end": "N/A", "duration_minutes": 0}

    def get_dur_muhurta(self, sunrise: str, sunset: str, tithi_data: Dict, day_of_week: int) -> List[Dict]:
        return get_dur_muhurta_data(sunrise, sunset, day_of_week)

    def get_varjyam_impl(
        self, sunrise: str, sunset: str, nakshatra_data: Dict, is_amrita: bool = False
    ) -> List[Dict]:
        from .timings import get_varjyam_impl_data

        return get_varjyam_impl_data(sunrise, sunset, nakshatra_data, is_amrita)

    def detect_special_days(self, tithi_data: Dict, vara: Dict, nakshatra: Dict) -> List[Dict]:
        """Detect day-level observances used by the display layer."""
        del vara, nakshatra  # Reserved for richer festival rules
        special_days: List[Dict] = []
        tithi_name = tithi_data.get("name", "")

        if tithi_name == "Ekadashi":
            special_days.append(
                {
                    "name": "Ekadashi",
                    "type": "fasting",
                    "importance": "major",
                    "description": "Fasting day dedicated to Lord Vishnu",
                    "observances": [
                        "Avoid all grains (rice, wheat, etc.)",
                        "Avoid beans and lentils",
                        "Avoid onion and garlic",
                        "Fruits and milk products are allowed",
                        "Sabudana, potato, sweet potato allowed",
                    ],
                    "benefits": [
                        "Spiritual purification",
                        "Removes sins",
                        "Improves health",
                        "Increases devotion",
                    ],
                }
            )

        if tithi_name == "Trayodashi":
            special_days.append(
                {
                    "name": "Pradosha Vrat",
                    "type": "worship",
                    "importance": "medium",
                    "description": "Auspicious time to worship Lord Shiva during twilight",
                    "observances": [
                        "Visit Shiva temple during sunset (5-7 PM)",
                        "Offer Bilva leaves",
                        "Chant Om Namah Shivaya",
                    ],
                    "benefits": ["Removes obstacles", "Brings peace and prosperity"],
                }
            )

        if tithi_name == "Chaturthi" and tithi_data.get("paksha") == "Krishna":
            special_days.append(
                {
                    "name": "Sankashta Chaturthi",
                    "type": "fasting",
                    "importance": "medium",
                    "description": "Day dedicated to Lord Ganesha",
                    "observances": [
                        "Fast throughout the day",
                        "Worship Ganesha in evening",
                        "Break fast after sighting moon",
                    ],
                    "benefits": ["Removes obstacles", "Success in endeavors"],
                }
            )

        if tithi_name == "Purnima":
            special_days.append(
                {
                    "name": "Purnima",
                    "type": "worship",
                    "importance": "major",
                    "description": "Full Moon day, auspicious for spiritual activities",
                    "observances": ["Meditation", "Charity", "Temple worship"],
                    "benefits": ["Mental peace", "Spiritual growth"],
                }
            )

        if tithi_name == "Amavasya":
            special_days.append(
                {
                    "name": "Amavasya",
                    "type": "ancestor",
                    "importance": "major",
                    "description": "New Moon day, sacred for ancestor worship",
                    "observances": ["Perform Tarpanam", "Offer food to ancestors"],
                    "benefits": ["Blessings of ancestors", "Family harmony"],
                }
            )

        return special_days

    def get_day_periods(self, sunrise: str, sunset: str, day_of_week: int) -> List[Dict]:
        """Calculate the 8 daytime periods with ruler and quality labels."""
        sunrise_min = time_to_minutes(sunrise)
        sunset_min = time_to_minutes(sunset)
        segment = (sunset_min - sunrise_min) / 8

        period_rulers = [
            {"name": "Sun", "quality": "neutral"},
            {"name": "Venus", "quality": "neutral"},
            {"name": "Mercury", "quality": "neutral"},
            {"name": "Moon", "quality": "neutral"},
            {"name": "Saturn", "quality": "neutral"},
            {"name": "Jupiter", "quality": "good"},
            {"name": "Mars", "quality": "neutral"},
            {"name": "Rahu", "quality": "neutral"},
        ]
        inauspicious_periods = {
            0: {"rahu": 7, "yama": 4, "gulika": 6},  # Sunday
            1: {"rahu": 1, "yama": 3, "gulika": 5},  # Monday
            2: {"rahu": 6, "yama": 2, "gulika": 4},  # Tuesday
            3: {"rahu": 4, "yama": 1, "gulika": 3},  # Wednesday
            4: {"rahu": 5, "yama": 0, "gulika": 2},  # Thursday
            5: {"rahu": 3, "yama": 6, "gulika": 1},  # Friday
            6: {"rahu": 2, "yama": 5, "gulika": 0},  # Saturday
        }

        day_map = inauspicious_periods.get(day_of_week, inauspicious_periods[0])
        periods: List[Dict] = []
        for idx in range(8):
            quality = period_rulers[idx]["quality"]
            note = ""
            special_type = None
            if idx == day_map.get("rahu"):
                quality = "rahu"
                note = "Rahu Kaal - Avoid new activities"
                special_type = "rahu"
            elif idx == day_map.get("yama"):
                quality = "yama"
                note = "Yamaganda - Inauspicious period"
                special_type = "yamaganda"
            elif idx == day_map.get("gulika"):
                quality = "gulika"
                note = "Gulika Kala - Inauspicious period"
                special_type = "gulika"
            elif quality == "good":
                note = "Generally favorable time"

            periods.append(
                {
                    "period": idx + 1,
                    "start": minutes_to_time(sunrise_min + (idx * segment)),
                    "end": minutes_to_time(sunrise_min + ((idx + 1) * segment)),
                    "ruler": period_rulers[idx]["name"],
                    "quality": quality,
                    "note": note,
                    "special_type": special_type,
                }
            )
        return periods

    def _build_south_india_special(
        self,
        festivals: List[Dict],
        karana: Dict,
        yoga: Dict,
        nakshatra: Dict,
    ) -> List[Dict]:
        """Build multilingual display notes to preserve previous rich display blocks."""
        festival_translations = {
            "Ekadashi": (
                "Ekadashi Fasting",
                "ಏಕಾದಶಿ ಉಪವಾಸ",
                "एकादशी व्रतम्",
            ),
            "Pradosha Vrat": (
                "Pradosha Vrat",
                "ಪ್ರದೋಷ ವ್ರತ",
                "प्रदोष व्रतम्",
            ),
            "Sankashta Chaturthi": (
                "Sankashta Chaturthi",
                "ಸಂಕಷ್ಟ ಚತುರ್ಥಿ",
                "संकष्ट चतुर्थी",
            ),
            "Purnima": (
                "Purnima Observance",
                "ಪೌರ್ಣಿಮಾ ಆಚರಣೆ",
                "पूर्णिमा पालनम्",
            ),
            "Amavasya": (
                "Amavasya Observance",
                "ಅಮಾವಾಸ್ಯೆ ಆಚರಣೆ",
                "अमावास्या पालनम्",
            ),
        }

        entries: List[Dict] = []
        for fest in festivals:
            en, kn, sa = festival_translations.get(
                fest.get("name", ""),
                (fest.get("name", "Festival"), fest.get("name", "Festival"), fest.get("name", "Festival")),
            )
            entries.append(
                {
                    "type": "festival",
                    "english": en,
                    "kannada": kn,
                    "sanskrit": sa,
                    "text": f"{en} | {kn} | {sa}",
                }
            )

        if karana.get("is_bhadra"):
            entries.append(
                {
                    "type": "note",
                    "english": f"Bhadra (Vishti Karana) - Avoid new beginnings upto {karana.get('end_time_formatted', 'end of period')}",
                    "kannada": f"ಭದ್ರ (ವಿಷ್ಟಿ ಕರಣ) - {karana.get('end_time_formatted', 'ಈ ಅವಧಿಯವರೆಗೆ')} ಹೊಸ ಕೆಲಸ ಆರಂಭಿಸಬೇಡಿ",
                    "sanskrit": "भद्र (विष्टि करण) - अशुभ आरम्भ त्याज्यः",
                    "text": (
                        f"Bhadra (Vishti Karana) - Avoid new beginnings upto {karana.get('end_time_formatted', 'end of period')} | "
                        f"ಭದ್ರ (ವಿಷ್ಟಿ ಕರಣ) - {karana.get('end_time_formatted', 'ಈ ಅವಧಿಯವರೆಗೆ')} ಹೊಸ ಕೆಲಸ ಆರಂಭಿಸಬೇಡಿ | "
                        "भद्र (विष्टि करण) - अशुभ आरम्भ त्याज्यः"
                    ),
                }
            )

        if nakshatra.get("name") in {"Moola", "Ashlesha", "Jyeshtha", "Magha", "Revati"}:
            entries.append(
                {
                    "type": "note",
                    "english": "Ganda Moola Nakshatra - Traditionally considered sensitive",
                    "kannada": "ಗಂಡ ಮೂಲ ನಕ್ಷತ್ರ - ಸಂಪ್ರದಾಯದ ಪ್ರಕಾರ ಸಂವೇದನಶೀಲ",
                    "sanskrit": "गण्ड मूल नक्षत्र - परम्परया संवेदनशीलम्",
                    "text": (
                        "Ganda Moola Nakshatra - Traditionally considered sensitive | "
                        "ಗಂಡ ಮೂಲ ನಕ್ಷತ್ರ - ಸಂಪ್ರದಾಯದ ಪ್ರಕಾರ ಸಂವೇದನಶೀಲ | "
                        "गण्ड मूल नक्षत्र - परम्परया संवेदनशीलम्"
                    ),
                }
            )

        if yoga.get("is_inauspicious"):
            entries.append(
                {
                    "type": "note",
                    "english": f"{yoga.get('name', 'Inauspicious')} Yoga - Inauspicious",
                    "kannada": f"{yoga.get('name', 'ಅಶುಭ')} ಯೋಗ - ಅಶುಭ",
                    "sanskrit": f"{yoga.get('name', 'अशुभ')} योगः - अशुभः",
                    "text": (
                        f"{yoga.get('name', 'Inauspicious')} Yoga - Inauspicious | "
                        f"{yoga.get('name', 'ಅಶುಭ')} ಯೋಗ - ಅಶುಭ | "
                        f"{yoga.get('name', 'अशुभ')} योगः - अशुभः"
                    ),
                }
            )

        return entries

    def _build_special_notes(self, tithi: Dict, nakshatra: Dict, yoga: Dict) -> Dict:
        """Compose summary/recommendation text for today's display."""
        recommendations: List[str] = []
        avoid: List[str] = []

        tithi_quality = tithi.get("quality", {})
        nak_quality = nakshatra.get("quality", {})
        recommendations.extend(tithi_quality.get("good_for", [])[:3])
        recommendations.extend(nak_quality.get("good_for", [])[:3])
        avoid.extend(tithi_quality.get("avoid", [])[:3])
        avoid.extend(nak_quality.get("avoid", [])[:3])
        if yoga.get("is_inauspicious"):
            avoid.append(f"Avoid major new ventures during {yoga.get('name', 'this')} Yoga")

        # De-duplicate while preserving order
        recommendations = list(dict.fromkeys([x for x in recommendations if x]))
        avoid = list(dict.fromkeys([x for x in avoid if x]))

        moon_note = (
            "Waxing Moon enhances positivity and growth"
            if tithi.get("paksha") == "Shukla"
            else "Waning Moon supports completion, reflection, and discipline"
        )
        summary = moon_note + "."
        if yoga.get("is_inauspicious"):
            summary += f" Avoid high-risk starts during {yoga.get('name')} Yoga."

        return {
            "summary": summary,
            "recommendations": recommendations,
            "avoid": avoid,
        }

    def _get_neighbor_nakshatras(self, nakshatra: Dict) -> List[Dict]:
        """Get previous/current/next nakshatra blocks for day-relevant Varjyam/Amrita selection."""
        candidates: List[Dict] = [nakshatra]
        dt_format = "%Y-%m-%d %H:%M:%S"

        prev_start = nakshatra.get("start_time")
        if prev_start:
            try:
                # Probe well inside previous Nakshatra to avoid edge-instability near transitions.
                probe_dt = datetime.strptime(prev_start, dt_format) - timedelta(hours=12)
                probe_jd = self.get_julian_day(probe_dt)
                candidates.append(self.get_nakshatra(probe_jd))
            except (ValueError, TypeError):
                pass

        # Build next candidate with full start/end by probing inside the expected next Nakshatra.
        next_name = nakshatra.get("next_nakshatra")
        next_start = nakshatra.get("end_time")
        if next_name and next_start:
            try:
                next_start_dt = datetime.strptime(next_start, dt_format)
                next_candidate = None
                for offset_hours in (6, 12, 18):
                    probe_dt = next_start_dt + timedelta(hours=offset_hours)
                    probe_jd = self.get_julian_day(probe_dt)
                    candidate = self.get_nakshatra(probe_jd)
                    if candidate.get("name") == next_name:
                        next_candidate = candidate
                        break
                    if next_candidate is None:
                        next_candidate = candidate

                if next_candidate:
                    candidates.append(next_candidate)
                else:
                    candidates.append({"name": next_name, "start_time": next_start})
            except (ValueError, TypeError):
                candidates.append({"name": next_name, "start_time": next_start})

        deduped: List[Dict] = []
        seen = set()
        for item in candidates:
            key = (item.get("name"), item.get("start_time"), item.get("end_time"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _select_events_for_date(self, events: List[Dict], target_date: date) -> List[Dict]:
        """Keep only events relevant to a calendar day; fallback to nearest event if none fall in day."""
        dt_format = "%Y-%m-%d %H:%M:%S"
        parsed: List[tuple] = []

        for event in events:
            start_dt = event.get("start_datetime")
            end_dt = event.get("end_datetime")
            try:
                start_parsed = datetime.strptime(start_dt, dt_format) if start_dt else None
                end_parsed = datetime.strptime(end_dt, dt_format) if end_dt else None
            except ValueError:
                continue
            if start_parsed:
                parsed.append((start_parsed, end_parsed, event))

        if not parsed:
            return []

        in_day = [
            event
            for start_parsed, end_parsed, event in parsed
            if start_parsed.date() == target_date
            or (end_parsed is not None and end_parsed.date() == target_date)
        ]
        if in_day:
            in_day.sort(key=lambda e: e.get("start_datetime", ""))
            return in_day

        # Fallback: closest event to local noon of target date
        noon = datetime.combine(target_date, datetime.min.time()) + timedelta(hours=12)
        closest = min(parsed, key=lambda p: abs((p[0] - noon).total_seconds()))
        return [closest[2]]

    def _select_events_for_day_window(
        self, events: List[Dict], day_start: datetime, day_end: datetime
    ) -> List[Dict]:
        """Select events overlapping the Vedic day window [sunrise, next sunrise)."""
        dt_format = "%Y-%m-%d %H:%M:%S"
        window_events: List[Dict] = []

        for event in events:
            start_dt = event.get("start_datetime")
            end_dt = event.get("end_datetime")
            try:
                start_parsed = datetime.strptime(start_dt, dt_format) if start_dt else None
                end_parsed = datetime.strptime(end_dt, dt_format) if end_dt else None
            except ValueError:
                continue

            if not start_parsed:
                continue

            event_end = end_parsed or (start_parsed + timedelta(minutes=event.get("duration_minutes", 96)))
            if start_parsed < day_end and event_end > day_start:
                window_events.append(event)

        if window_events:
            window_events.sort(key=lambda e: e.get("start_datetime", ""))
            return window_events

        return self._select_events_for_date(events, day_start.date())

    def calculate_panchang(
        self,
        dt: datetime,
        lat: float = 12.9716,
        lon: float = 77.5946,
        city: str = "Bengaluru",
    ) -> Dict:
        """
        Calculate complete Panchang for a given date and location.

        Note:
            This returns both the modular keys (`kaala`, `muhurat`) and the
            legacy display keys (`inauspicious_times`, `auspicious_times`, etc.)
            to preserve backward compatibility with existing UI rendering.
        """
        jd = self.get_julian_day(dt)

        # Core data
        tithi = self.get_tithi(jd)
        nakshatra = self.get_nakshatra(jd)
        yoga = self.get_yoga(jd)
        karana = self.get_karana(jd)
        vara = self.get_vara(dt)

        # Sun and Moon
        sun_times = self.get_sun_rise_set(dt, lat, lon)
        moon_times = self.get_moon_rise_set(dt, lat, lon)
        moon_sign = self.get_moon_sign(jd)

        # Calendar info
        ayana = self.get_ayana(jd)
        ruthu = self.get_ruthu(jd)
        calendar_info = self.get_hindu_calendar_info(dt, jd, tithi)
        samvatsara = {
            "name": calendar_info.get("samvatsara_name"),
            "cycle_year": calendar_info.get("samvatsara_cycle_year"),
            "shaka_year": calendar_info.get("shaka_year"),
            "kali_year": dt.year + 3102,
            "number": calendar_info.get("samvatsara_cycle_year"),
        }

        day_of_week = vara["number"] - 1

        # Timings
        rahu = self.get_rahu_kala(sun_times["sunrise"], sun_times["sunset"], day_of_week)
        yamaganda = self.get_yamaganda(sun_times["sunrise"], sun_times["sunset"], day_of_week)
        gulika = self.get_gulika(sun_times["sunrise"], sun_times["sunset"], day_of_week)
        abhijit = self.get_abhijit_muhurat(sun_times["sunrise"], sun_times["sunset"])
        brahma = self.get_brahma_muhurat(sun_times["sunrise"])
        dur_muhurta = self.get_dur_muhurta(
            sun_times["sunrise"], sun_times["sunset"], tithi, day_of_week
        )
        nakshatra_candidates = self._get_neighbor_nakshatras(nakshatra)
        varjyam_events: List[Dict] = []
        amrita_events: List[Dict] = []
        for candidate in nakshatra_candidates:
            varjyam_events.extend(
                self.get_varjyam_impl(sun_times["sunrise"], sun_times["sunset"], candidate)
            )
            amrita_events.extend(
                self.get_varjyam_impl(
                    sun_times["sunrise"], sun_times["sunset"], candidate, is_amrita=True
                )
            )

        def _dedupe_events(events: List[Dict]) -> List[Dict]:
            deduped: List[Dict] = []
            seen = set()
            for event in events:
                key = (event.get("start_datetime"), event.get("end_datetime"), event.get("description"))
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(event)
            return deduped

        varjyam_events = _dedupe_events(varjyam_events)
        amrita_events = _dedupe_events(amrita_events)

        try:
            day_start = datetime.strptime(
                f"{dt.strftime('%Y-%m-%d')} {sun_times['sunrise']}", "%Y-%m-%d %H:%M:%S"
            )
            day_end = day_start + timedelta(days=1)
            varjyam = self._select_events_for_day_window(varjyam_events, day_start, day_end)
            amrita_selected = self._select_events_for_day_window(amrita_events, day_start, day_end)
        except (KeyError, TypeError, ValueError):
            varjyam = self._select_events_for_date(varjyam_events, dt.date())
            amrita_selected = self._select_events_for_date(amrita_events, dt.date())

        if not varjyam:
            varjyam = self.get_varjyam_impl(sun_times["sunrise"], sun_times["sunset"], nakshatra)
        if not amrita_selected:
            amrita_fallback = self.get_varjyam_impl(
                sun_times["sunrise"], sun_times["sunset"], nakshatra, is_amrita=True
            )
            amrita = (
                amrita_fallback[0]
                if amrita_fallback
                else {"start": "N/A", "end": "N/A", "duration_minutes": 0}
            )
        else:
            amrita = amrita_selected[0]

        # Add quality indicators
        tithi["quality"] = self.get_tithi_quality(tithi["name"])
        nakshatra["quality"] = self.get_nakshatra_quality(nakshatra["name"])

        festivals = self.detect_special_days(tithi, vara, nakshatra)
        day_periods = self.get_day_periods(sun_times["sunrise"], sun_times["sunset"], day_of_week)
        south_india_special = self._build_south_india_special(festivals, karana, yoga, nakshatra)
        special_notes = self._build_special_notes(tithi, nakshatra, yoga)

        # Metadata
        swe.set_sid_mode(swe.SIDM_LAHIRI)
        ayanamsa = swe.get_ayanamsa_ex(jd, swe.FLG_SIDEREAL)[1]

        inauspicious_times = {
            "rahu_kaal": rahu,
            "yamaganda": yamaganda,
            "gulika": gulika,
        }
        auspicious_times = {
            "abhijit_muhurat": abhijit,
            "brahma_muhurat": brahma,
            "amrita_kalam": amrita,
        }
        additional_inauspicious_times = {
            "dur_muhurta": dur_muhurta,
            "varjyam": varjyam,
        }

        hindu_display = {
            **calendar_info,
            # Backward-compatible aliases used by frontend rendering
            "samvat_vikram": calendar_info.get("vikram_samvat"),
            "samvat_shaka": calendar_info.get("shaka_samvat"),
            "shaka_year": calendar_info.get("shaka_year"),
            "month": calendar_info.get("lunar_month_purnimanta"),
            "samvatsara": samvatsara,
        }

        return {
            "date": {
                "gregorian": {
                    "date": dt.strftime("%Y-%m-%d"),
                    "day": dt.strftime("%A"),
                    "day_of_week": vara["name"],
                    "formatted": dt.strftime("%A, %d %B, %Y"),
                },
                "hindu": hindu_display,
            },
            "location": {
                "city": city,
                "latitude": lat,
                "longitude": lon,
                "timezone": "Asia/Kolkata",
            },
            "sun_moon": {
                "sunrise": sun_times["sunrise"],
                "sunset": sun_times["sunset"],
                "moonrise": moon_times["moonrise"],
                "moonset": moon_times["moonset"],
            },
            "panchang": {
                "tithi": tithi,
                "nakshatra": nakshatra,
                "yoga": yoga,
                "karana": karana,
                "vara": vara,
            },
            "moon_sign": moon_sign,
            "ayana": ayana,
            "ruthu": ruthu,
            "samvatsara": samvatsara,
            # Legacy display contract used by frontend
            "inauspicious_times": inauspicious_times,
            "auspicious_times": auspicious_times,
            "additional_inauspicious_times": additional_inauspicious_times,
            "day_periods": day_periods,
            "festivals": festivals,
            "south_india_special": south_india_special,
            "special_notes": special_notes,
            # Modular contract retained for downstream usage
            "kaala": {
                "rahu": rahu,
                "yamaganda": yamaganda,
                "gulika": gulika,
                "dur_muhurta": dur_muhurta,
                "varjyam": varjyam[0] if varjyam else {"start": "N/A", "end": "N/A"},
                "amrita": amrita,
            },
            "muhurat": {
                "abhijit": abhijit,
                "brahma": brahma,
            },
            "calculation_metadata": {
                "ayanamsa_value": ayanamsa,
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "verified_against": "Swiss Ephemeris 2.10 (Lahiri Ayanamsa)",
                "ayanamsa_name": "Lahiri",
                # Timing confidence flags: hide experimental values in strict display mode
                "amrita_kalam_verified": False,
                "varjyam_verified": False,
                # Preview flags: enabled so temple team can manually validate over a few days.
                "amrita_kalam_preview": True,
                "varjyam_preview": True,
            },
        }
