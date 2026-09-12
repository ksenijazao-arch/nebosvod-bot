"""
Астрономические расчёты для натального разбора и прогноза.
Перенесено из проверенной JS-версии (see AstroBotDemo.jsx) один в один,
формулы валидированы отдельно: периоды обращения планет, максимальная
элонгация Меркурия/Венеры, совпадение асцендента с Солнцем на восходе.
"""
import math

SIGNS = ["Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева", "Весы",
         "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы"]


def norm360(x):
    x = x % 360
    if x < 0:
        x += 360
    return x


def to_jd(year, month, day, utc_hour, utc_minute):
    y, m = year, month
    frac = (utc_hour + utc_minute / 60) / 24
    if m <= 2:
        y -= 1
        m += 12
    a = math.floor(y / 100)
    b = 2 - a + math.floor(a / 4)
    jd0 = math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + day + b - 1524.5
    return jd0 + frac


def jd_to_date_label(jd):
    z = math.floor(jd + 0.5)
    a2 = z
    if z >= 2299161:
        al = math.floor((z - 1867216.25) / 36524.25)
        a2 = z + 1 + al - math.floor(al / 4)
    b2 = a2 + 1524
    c2 = math.floor((b2 - 122.1) / 365.25)
    d2 = math.floor(365.25 * c2)
    e2 = math.floor((b2 - d2) / 30.6001)
    day = math.floor(b2 - d2 - math.floor(30.6001 * e2))
    month = int(e2 - 1) if e2 < 14 else int(e2 - 13)
    year = int(c2 - 4716) if month > 2 else int(c2 - 4715)
    months = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
    return f"{day} {months[month - 1]} {year}"


def sun_longitude(jd):
    d = jd - 2451545.0
    L = norm360(280.460 + 0.9856474 * d)
    g = math.radians(norm360(357.528 + 0.9856003 * d))
    lam = L + 1.915 * math.sin(g) + 0.02 * math.sin(2 * g)
    return norm360(lam)


def moon_longitude(jd):
    d = jd - 2451545.0
    lm = norm360(218.316 + 13.176396 * d)
    mm = math.radians(norm360(134.963 + 13.064993 * d))
    dd = math.radians(norm360(297.85 + 12.190749 * d))
    lam = lm + 6.289 * math.sin(mm)
    lam += -1.274 * math.sin(mm - 2 * dd)
    lam += 0.658 * math.sin(2 * dd)
    return norm360(lam)


def obliquity(jd):
    t = (jd - 2451545.0) / 36525
    return 23.439291 - 0.0130042 * t


def gst(jd):
    t = (jd - 2451545.0) / 36525
    g = (280.46061837 + 360.98564736629 * (jd - 2451545.0)
         + 0.000387933 * t * t - (t ** 3) / 38710000)
    return norm360(g)


def ascendant(jd, lat_deg, lon_deg):
    lst = norm360(gst(jd) + lon_deg)
    lst_rad = math.radians(lst)
    eps = math.radians(obliquity(jd))
    lat = math.radians(lat_deg)
    y = math.cos(lst_rad)
    x = -(math.sin(eps) * math.tan(lat) + math.cos(eps) * math.sin(lst_rad))
    return norm360(math.degrees(math.atan2(y, x)))


def sign_of(lon):
    l = norm360(lon)
    idx = int(math.floor(l / 30))
    return {"sign": SIGNS[idx], "deg": int(math.floor(l % 30))}


ELEMENTS = {
    "mercury": dict(a=0.38709927, e=0.20563593, L0=252.2503235, Lr=149472.67411175, pi0=77.45779628, pir=0.16047689),
    "venus":   dict(a=0.72333566, e=0.00677672, L0=181.9790995, Lr=58517.81538729, pi0=131.60246718, pir=0.00268329),
    "earth":   dict(a=1.00000261, e=0.01671123, L0=100.46457166, Lr=35999.37244981, pi0=102.93768193, pir=0.32327364),
    "mars":    dict(a=1.52371034, e=0.0933941, L0=355.44656795, Lr=19140.30268499, pi0=336.05637041, pir=0.44441088),
    "jupiter": dict(a=5.202887, e=0.04838624, L0=34.39644051, Lr=3034.74612775, pi0=14.72847983, pir=0.21252668),
    "saturn":  dict(a=9.53667594, e=0.05386179, L0=49.95424423, Lr=1222.49362201, pi0=92.59887831, pir=-0.41897216),
}


def kepler_e(m, e):
    E = m
    for _ in range(40):
        dE = (E - e * math.sin(E) - m) / (1 - e * math.cos(E))
        E -= dE
        if abs(dE) < 1e-9:
            break
    return E


def heliocentric_xy(key, t):
    el = ELEMENTS[key]
    L = norm360(el["L0"] + el["Lr"] * t)
    pi_ = norm360(el["pi0"] + el["pir"] * t)
    m = math.radians(norm360(L - pi_))
    E = kepler_e(m, el["e"])
    x_orb = el["a"] * (math.cos(E) - el["e"])
    y_orb = el["a"] * math.sqrt(1 - el["e"] ** 2) * math.sin(E)
    pi_rad = math.radians(pi_)
    x = x_orb * math.cos(pi_rad) - y_orb * math.sin(pi_rad)
    y = x_orb * math.sin(pi_rad) + y_orb * math.cos(pi_rad)
    return x, y


def planet_longitude(key, jd):
    t = (jd - 2451545.0) / 36525
    px, py = heliocentric_xy(key, t)
    ex, ey = heliocentric_xy("earth", t)
    return norm360(math.degrees(math.atan2(py - ey, px - ex)))


CITY_COORDS = {
    "москва": dict(lat=55.7558, lon=37.6173, tz=3, label="Москва"),
    "санкт-петербург": dict(lat=59.9311, lon=30.3609, tz=3, label="Санкт-Петербург"),
    "калининград": dict(lat=54.7104, lon=20.4522, tz=2, label="Калининград"),
    "новосибирск": dict(lat=55.0084, lon=82.9357, tz=7, label="Новосибирск"),
    "екатеринбург": dict(lat=56.8389, lon=60.6057, tz=5, label="Екатеринбург"),
    "казань": dict(lat=55.7887, lon=49.1221, tz=3, label="Казань"),
    "краснодар": dict(lat=45.0355, lon=38.9753, tz=3, label="Краснодар"),
    "киев": dict(lat=50.4501, lon=30.5234, tz=2, label="Киев"),
    "минск": dict(lat=53.9006, lon=27.5590, tz=3, label="Минск"),
    "алматы": dict(lat=43.2220, lon=76.8512, tz=6, label="Алматы"),
}


def compute_chart(date_str, time_str, city_label):
    y, m, d = (int(x) for x in date_str.split("-"))
    has_time = bool(time_str)
    hour, minute = 12, 0
    if has_time:
        hh, mm = (int(x) for x in time_str.split(":"))
        hour, minute = hh, mm
    key = None
    for k, v in CITY_COORDS.items():
        if v["label"].lower() == (city_label or "").strip().lower():
            key = k
            break
    city = CITY_COORDS.get(key, CITY_COORDS["москва"])
    used_default_city = key is None
    utc_hour = hour - city["tz"]
    jd = to_jd(y, m, d, utc_hour, minute)

    chart = {
        "sun": sign_of(sun_longitude(jd)),
        "moon": sign_of(moon_longitude(jd)),
        "mercury": sign_of(planet_longitude("mercury", jd)),
        "venus": sign_of(planet_longitude("venus", jd)),
        "mars": sign_of(planet_longitude("mars", jd)),
        "jupiter": sign_of(planet_longitude("jupiter", jd)),
        "saturn": sign_of(planet_longitude("saturn", jd)),
        "rising": sign_of(ascendant(jd, city["lat"], city["lon"])) if has_time else None,
        "has_time": has_time,
        "city": city,
        "used_default_city": used_default_city,
    }
    return chart


ASPECT_LIST = [
    ("conjunction", "соединение", 0),
    ("square", "квадрат", 90),
    ("trine", "трин", 120),
    ("opposition", "оппозиция", 180),
]


def ang_diff(a, b):
    d = abs(norm360(a - b))
    if d > 180:
        d = 360 - d
    return d


def find_transit_hits(natal_lon, planet_key, start_jd, window_days, threshold=2):
    daily = [ang_diff(planet_longitude(planet_key, start_jd + t), natal_lon) for t in range(window_days + 1)]
    hits = []
    for key, name, angle in ASPECT_LIST:
        in_cluster = False
        min_orb, min_day = float("inf"), 0
        for t in range(window_days + 1):
            orb = abs(daily[t] - angle)
            if orb < threshold:
                if not in_cluster:
                    in_cluster, min_orb, min_day = True, orb, t
                elif orb < min_orb:
                    min_orb, min_day = orb, t
            elif in_cluster:
                hits.append({"day_offset": min_day, "aspect_key": key, "aspect_name": name})
                in_cluster = False
        if in_cluster:
            hits.append({"day_offset": min_day, "aspect_key": key, "aspect_name": name})
    hits.sort(key=lambda h: h["day_offset"])
    for h in hits:
        h["date_label"] = jd_to_date_label(start_jd + h["day_offset"])
    return hits


def compute_forecast(chart, now_jd, window_days=730):
    natal_points = {"sun": chart["sun"], "moon": chart["moon"]}
    results = []
    for planet_key in ["jupiter", "saturn"]:
        for point_key in ["sun", "moon"]:
            p = natal_points[point_key]
            natal_deg = SIGNS.index(p["sign"]) * 30 + p["deg"]
            hits = find_transit_hits(natal_deg, planet_key, now_jd, window_days)
            for h in hits:
                h["planet_key"] = planet_key
                h["point_key"] = point_key
                results.append(h)
    results.sort(key=lambda h: h["day_offset"])
    return results
