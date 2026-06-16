"""Weather via the free Open-Meteo API (no key needed).

Provides current conditions, hourly forecast, sunset/lights warnings, and an
AI-free "playability" rating for outdoor tennis. Results are cached in memory
for 30 minutes so a background poller can refresh without hammering the API.
"""

import threading
from datetime import datetime

import requests

from bot.logger import log_error, log_info, load_settings
from app import now_sydney

BASE_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
CACHE_MINUTES = 30
REQUEST_TIMEOUT = 12

_weather_cache = {"data": None, "fetched_at": None}
_week_cache = {"data": None, "fetched_at": None, "lat": None, "lon": None}
_CACHE_LOCK = threading.Lock()

_COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']

_WMO = {
    0: "Sunny",
    1: "Partly Cloudy", 2: "Partly Cloudy", 3: "Partly Cloudy",
    45: "Foggy", 48: "Foggy",
    51: "Light Rain", 53: "Light Rain", 55: "Light Rain",
    56: "Light Rain", 57: "Light Rain",
    61: "Rainy", 63: "Rainy", 65: "Rainy",
    66: "Rainy", 67: "Rainy",
    71: "Snow", 73: "Snow", 75: "Snow", 77: "Snow",
    80: "Showers", 81: "Showers", 82: "Showers",
    85: "Snow", 86: "Snow",
    95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm",
}


def wind_degrees_to_compass(degrees) -> str:
    """Convert a wind bearing in degrees to an 8-point compass label."""
    try:
        deg = float(degrees)
    except (TypeError, ValueError):
        return "N"
    idx = int((deg % 360) / 45 + 0.5) % 8
    return _COMPASS[idx]


def wmo_code_to_condition(code) -> str:
    """Map a WMO weather code to a short human-readable condition."""
    try:
        return _WMO.get(int(code), "Cloudy")
    except (TypeError, ValueError):
        return "Cloudy"


def get_uv_label(uv) -> str:
    """Return the standard UV exposure category for a UV index value."""
    try:
        value = float(uv)
    except (TypeError, ValueError):
        return "Low"
    if value < 3:
        return "Low"
    if value < 6:
        return "Moderate"
    if value < 8:
        return "High"
    if value < 11:
        return "Very High"
    return "Extreme"


def get_playability(wind_kmh, rain_prob, temp_c) -> dict:
    """Rate outdoor tennis conditions from wind, rain chance, and temperature."""
    wind = float(wind_kmh or 0)
    rain = float(rain_prob or 0)
    temp = float(temp_c or 0)
    if wind > 30 or rain > 50 or temp < 10 or temp > 38:
        return {
            "rating": "Poor",
            "colour": "red",
            "message": "Consider rescheduling today's outdoor lessons",
        }
    if wind > 20 or rain > 20:
        return {
            "rating": "Marginal",
            "colour": "amber",
            "message": "Playable but conditions may be difficult",
        }
    return {
        "rating": "Good",
        "colour": "green",
        "message": "Great conditions for tennis today",
    }


def get_lights_warning(sunset_str, warning_minutes: int = 45):
    """Return {minutes_until, sunset_time} if sunset is within the window, else None."""
    if not sunset_str:
        return None
    try:
        hh, mm = str(sunset_str).split(":")[:2]
        now = now_sydney()
        sunset = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except (ValueError, TypeError):
        return None
    minutes_until = int((sunset - now_sydney()).total_seconds() // 60)
    if 0 <= minutes_until <= warning_minutes:
        return {"minutes_until": minutes_until, "sunset_time": sunset_str}
    return None


def _hhmm(iso_string: str) -> str:
    """Pull 'HH:MM' out of an ISO datetime string like '2025-06-15T17:47'."""
    if not iso_string:
        return ""
    try:
        return datetime.fromisoformat(iso_string).strftime("%H:%M")
    except (ValueError, TypeError):
        return str(iso_string)[-5:]


def get_weather(lat: float, lon: float):
    """Fetch and shape today's weather for the given coordinates, or None on failure."""
    settings = load_settings()
    warning_minutes = int(settings.get("lights_warning_minutes", 45))
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join([
            "temperature_2m", "apparent_temperature", "precipitation_probability",
            "wind_speed_10m", "wind_direction_10m", "weather_code", "uv_index",
        ]),
        "hourly": ",".join([
            "temperature_2m", "precipitation_probability", "wind_speed_10m", "uv_index",
        ]),
        "daily": ",".join([
            "sunrise", "sunset", "precipitation_probability_max",
            "wind_speed_10m_max", "uv_index_max",
        ]),
        "timezone": "Australia/Sydney",
        "forecast_days": 1,
        "wind_speed_unit": "kmh",
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        log_error(f"Weather fetch failed: {e}")
        return None

    current = raw.get("current", {})
    daily = raw.get("daily", {})
    hourly = raw.get("hourly", {})

    temp_c = float(current.get("temperature_2m") or 0)
    feels = float(current.get("apparent_temperature") or temp_c)
    rain_prob = int(current.get("precipitation_probability") or 0)
    wind_kmh = float(current.get("wind_speed_10m") or 0)
    wind_dir_deg = current.get("wind_direction_10m") or 0
    code = current.get("weather_code") or 0
    uv = float(current.get("uv_index") or 0)

    sunrise = _hhmm((daily.get("sunrise") or [""])[0])
    sunset = _hhmm((daily.get("sunset") or [""])[0])

    hourly_out = []
    times = hourly.get("time", []) or []
    temps = hourly.get("temperature_2m", []) or []
    rains = hourly.get("precipitation_probability", []) or []
    winds = hourly.get("wind_speed_10m", []) or []
    now = now_sydney()
    start_idx = 0
    for i, t in enumerate(times):
        try:
            if datetime.fromisoformat(t).hour >= now.hour:
                start_idx = i
                break
        except (ValueError, TypeError):
            continue
    for i in range(start_idx, min(start_idx + 6, len(times))):
        hourly_out.append({
            "hour": _hhmm(times[i]),
            "temp": round(float(temps[i])) if i < len(temps) else None,
            "rain_prob": int(rains[i]) if i < len(rains) else 0,
            "wind": round(float(winds[i])) if i < len(winds) else 0,
        })

    return {
        "temp_c": round(temp_c, 1),
        "feels_like_c": round(feels, 1),
        "condition": wmo_code_to_condition(code),
        "wind_kmh": round(wind_kmh, 1),
        "wind_direction": wind_degrees_to_compass(wind_dir_deg),
        "rain_prob": rain_prob,
        "uv_index": round(uv, 1),
        "uv_label": get_uv_label(uv),
        "sunset_time": sunset,
        "sunrise_time": sunrise,
        "playability": get_playability(wind_kmh, rain_prob, temp_c),
        "lights_warning": get_lights_warning(sunset, warning_minutes),
        "hourly": hourly_out,
    }


def refresh_weather_cache() -> None:
    """Fetch fresh weather for the configured location and store it in the cache."""
    settings = load_settings()
    lat = settings.get("latitude", -33.8688)
    lon = settings.get("longitude", 151.2093)
    data = get_weather(lat, lon)
    if data is not None:
        with _CACHE_LOCK:
            _weather_cache["data"] = data
            _weather_cache["fetched_at"] = now_sydney()
        log_info("Weather cache refreshed.")


def get_cached_weather():
    """Return cached weather, refreshing if older than CACHE_MINUTES."""
    with _CACHE_LOCK:
        data = _weather_cache["data"]
        fetched = _weather_cache["fetched_at"]
    fresh = (
        data is not None
        and fetched is not None
        and (now_sydney() - fetched).total_seconds() < CACHE_MINUTES * 60
    )
    if fresh:
        return data
    refresh_weather_cache()
    with _CACHE_LOCK:
        return _weather_cache["data"]


def clear_weather_cache() -> None:
    """Drop cached weather/forecast so the next read re-fetches (e.g. after a location change)."""
    with _CACHE_LOCK:
        _weather_cache["data"] = None
        _weather_cache["fetched_at"] = None
        _week_cache["data"] = None
        _week_cache["fetched_at"] = None
        _week_cache["lat"] = None
        _week_cache["lon"] = None


# ---------------------------------------------------------------------------
# Geocoding (location search)
# ---------------------------------------------------------------------------

def geocode_search(query: str):
    """Search place names via Open-Meteo Geocoding (free, no key).

    Returns a list of {name, display, lat, lon, country, admin1}. Empty list on
    no match or error. ``display`` looks like "Panania, New South Wales, Australia".
    """
    query = (query or "").strip()
    if len(query) < 2:
        return []
    params = {
        "name": query,
        "count": 8,
        "language": "en",
        "format": "json",
    }
    try:
        resp = requests.get(GEOCODE_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        log_error(f"Geocode failed for {query!r}: {e}")
        return []

    out = []
    for r in raw.get("results", []) or []:
        name = r.get("name") or ""
        admin1 = r.get("admin1") or ""
        country = r.get("country") or ""
        parts = [p for p in (name, admin1, country) if p]
        out.append({
            "name": name,
            "display": ", ".join(parts),
            "lat": r.get("latitude"),
            "lon": r.get("longitude"),
            "country": country,
            "admin1": admin1,
        })
    return out


# ---------------------------------------------------------------------------
# 7-day forecast
# ---------------------------------------------------------------------------

def _week_lessons_by_date():
    """Return {date_str: count} of lessons that count toward 'at risk' (scheduled/completed)."""
    counts = {}
    try:
        from bot.logger import load_json
        from app import LESSONS_PATH
        data = load_json(LESSONS_PATH, {"lessons": []})
        for lesson in data.get("lessons", []) or []:
            status = (lesson.get("status") or "").lower()
            if status in ("cancelled", "canceled"):
                continue
            d = lesson.get("date")
            if d:
                counts[d] = counts.get(d, 0) + 1
    except Exception as e:
        log_error(f"Week lessons count failed: {e}")
    return counts


def get_week_forecast(lat: float, lon: float):
    """Fetch a 7-day daily forecast shaped for the coach home + calendar.

    Each day: date, day_name, condition, temp_max, temp_min, rain_prob,
    wind_max, sunrise, sunset, playability {rating, colour}, lessons_count,
    lessons_at_risk. Returns {'days': [...]} or None on failure.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": ",".join([
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "precipitation_probability_max", "wind_speed_10m_max",
            "sunrise", "sunset",
        ]),
        "timezone": "Australia/Sydney",
        "forecast_days": 7,
        "wind_speed_unit": "kmh",
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        log_error(f"Week forecast fetch failed: {e}")
        return None

    daily = raw.get("daily", {}) or {}
    dates = daily.get("time", []) or []
    codes = daily.get("weather_code", []) or []
    tmax = daily.get("temperature_2m_max", []) or []
    tmin = daily.get("temperature_2m_min", []) or []
    rain = daily.get("precipitation_probability_max", []) or []
    wind = daily.get("wind_speed_10m_max", []) or []
    sunrises = daily.get("sunrise", []) or []
    sunsets = daily.get("sunset", []) or []

    lessons_by_date = _week_lessons_by_date()
    today = now_sydney().date().isoformat()
    days = []
    for i, date_str in enumerate(dates):
        try:
            d = datetime.fromisoformat(date_str).date()
            day_name = d.strftime("%A")
        except (ValueError, TypeError):
            day_name = ""
        rain_prob = int(rain[i]) if i < len(rain) and rain[i] is not None else 0
        wind_max = round(float(wind[i])) if i < len(wind) and wind[i] is not None else 0
        t_max = round(float(tmax[i])) if i < len(tmax) and tmax[i] is not None else None
        t_min = round(float(tmin[i])) if i < len(tmin) and tmin[i] is not None else None
        play = get_playability(wind_max, rain_prob, t_max if t_max is not None else 20)
        lessons_count = lessons_by_date.get(date_str, 0)
        days.append({
            "date": date_str,
            "day_name": day_name,
            "is_today": date_str == today,
            "condition": wmo_code_to_condition(codes[i]) if i < len(codes) else "Cloudy",
            "temp_max": t_max,
            "temp_min": t_min,
            "rain_prob": rain_prob,
            "wind_max": wind_max,
            "sunrise": _hhmm(sunrises[i]) if i < len(sunrises) else "",
            "sunset": _hhmm(sunsets[i]) if i < len(sunsets) else "",
            "playability": {"rating": play["rating"], "colour": play["colour"]},
            "lessons_count": lessons_count,
            "lessons_at_risk": rain_prob > 40 and lessons_count > 0,
        })
    return {"days": days}


def get_cached_week_forecast():
    """Return a cached 7-day forecast for the configured location, refreshing per CACHE_MINUTES."""
    settings = load_settings()
    lat = settings.get("latitude", -33.8688)
    lon = settings.get("longitude", 151.2093)
    with _CACHE_LOCK:
        data = _week_cache["data"]
        fetched = _week_cache["fetched_at"]
        same_loc = _week_cache["lat"] == lat and _week_cache["lon"] == lon
    fresh = (
        data is not None
        and fetched is not None
        and same_loc
        and (now_sydney() - fetched).total_seconds() < CACHE_MINUTES * 60
    )
    if fresh:
        return data
    data = get_week_forecast(lat, lon)
    if data is not None:
        with _CACHE_LOCK:
            _week_cache["data"] = data
            _week_cache["fetched_at"] = now_sydney()
            _week_cache["lat"] = lat
            _week_cache["lon"] = lon
    return data
