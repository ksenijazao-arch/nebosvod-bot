"""
Астрономические расчёты для натального разбора и прогноза.
Перенесено из проверенной JS-версии (see AstroBotDemo.jsx) один в один,
формулы валидированы отдельно: периоды обращения планет, максимальная
элонгация Меркурия/Венеры, совпадение асцендента с Солнцем на восходе.
"""
import json
import math
import os
from datetime import datetime
from zoneinfo import ZoneInfo

SIGNS = ["Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева", "Весы",
         "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы"]

SIGN_PREPOSITIONAL = {
    "Овен": "Овне", "Телец": "Тельце", "Близнецы": "Близнецах", "Рак": "Раке",
    "Лев": "Льве", "Дева": "Деве", "Весы": "Весах", "Скорпион": "Скорпионе",
    "Стрелец": "Стрельце", "Козерог": "Козероге", "Водолей": "Водолее", "Рыбы": "Рыбах",
}
SIGN_GENITIVE = {
    "Овен": "Овна", "Телец": "Тельца", "Близнецы": "Близнецов", "Рак": "Рака",
    "Лев": "Льва", "Дева": "Девы", "Весы": "Весов", "Скорпион": "Скорпиона",
    "Стрелец": "Стрельца", "Козерог": "Козерога", "Водолей": "Водолея", "Рыбы": "Рыб",
}


def v_predlog(sign):
    """Предлог 'в'/'во' перед знаком (Лев -> 'во Льве', остальные -> 'в X')."""
    return "во" if sign == "Лев" else "в"


_CITIES_PATH = os.path.join(os.path.dirname(__file__), "cities_data.json")
with open(_CITIES_PATH, encoding="utf-8") as _f:
    _CITY_INDEX = json.load(_f)


def geocode_city(name):
    """Ищет город по названию (любому известному варианту, включая кириллицу).
    Возвращает {'name','lat','lon','tz'} или None, если не нашлось."""
    if not name:
        return None
    return _CITY_INDEX.get(name.strip().lower())


def utc_offset_hours(tz_name, year, month, day, hour, minute):
    """Настоящее историческое смещение от UTC для конкретной даты и часового
    пояса (учитывает переходы на летнее время и реформы прошлых лет, а не
    только текущее смещение)."""
    dt = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz_name))
    return dt.utcoffset().total_seconds() / 3600


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


def jd_to_ymd(jd):
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
    return year, month, day


ELEMENT_OF_SIGN = {}
MODALITY_OF_SIGN = {}
_elements_cycle = ["fire", "earth", "air", "water"]
_modality_cycle = ["cardinal", "fixed", "mutable"]
for _i, _s in enumerate(SIGNS):
    ELEMENT_OF_SIGN[_s] = _elements_cycle[_i % 4]
    MODALITY_OF_SIGN[_s] = _modality_cycle[_i % 3]


def life_path_number(day, month, year):
    """Число жизненного пути: сумма всех цифр даты рождения, сворачивается
    до одной цифры, кроме мастер-чисел 11/22/33, которые не сворачиваются
    дальше на любом шаге, если встретились."""
    return life_path_steps(day, month, year)[-1]


def life_path_steps(day, month, year):
    """Та же логика, но возвращает список всех шагов свёртки, включая
    исходную сумму цифр и финальное число, для наглядного показа расчёта."""
    digits = [int(c) for c in f"{day:02d}{month:02d}{year:04d}"]
    total = sum(digits)
    steps = [total]
    while total not in (11, 22, 33) and total > 9:
        total = sum(int(c) for c in str(total))
        steps.append(total)
    return steps


# проверенные даты и долготы затмений на 2026-2028, сверены по нескольким независимым источникам
ECLIPSES = [
    (2026, 2, 17, "solar", 328.83),
    (2026, 3, 3, "lunar", 162.90),
    (2026, 8, 12, "solar", 140.03),
    (2026, 8, 28, "lunar", 334.90),
    (2027, 2, 6, "solar", 317.63),
    (2027, 2, 20, "lunar", 152.10),
    (2027, 7, 18, "lunar", 295.82),
    (2027, 8, 2, "solar", 129.92),
    (2027, 8, 17, "lunar", 324.20),
    (2028, 1, 11, "lunar", 111.47),
    (2028, 1, 26, "solar", 306.18),
    (2028, 7, 6, "lunar", 285.18),
    (2028, 7, 21, "solar", 119.85),
    (2028, 12, 31, "lunar", 100.55),
]


def find_personal_eclipses(natal_points, now_jd, orb=5):
    """Ищет затмения из таблицы ECLIPSES, которые попадают в заданный орбис
    к одной из натальных точек человека (обычно Солнце, Луна, асцендент).
    natal_points, словарь вида {"sun": долгота, "moon": долгота, ...}.
    Возвращает только будущие затмения, от now_jd и дальше."""
    hits = []
    for y, m, d, etype, ecl_lon in ECLIPSES:
        ecl_jd = to_jd(y, m, d, 12, 0)
        if ecl_jd < now_jd:
            continue
        for point_name, point_lon_value in natal_points.items():
            diff = abs(norm360(ecl_lon - point_lon_value))
            if diff > 180:
                diff = 360 - diff
            if diff <= orb:
                hits.append({"jd": ecl_jd, "type": etype, "point": point_name, "orb": round(diff, 1)})
                break
    return hits


def equation_of_time_minutes(jd):
    """Уравнение времени: разница между истинным и средним солнечным
    полднем, до 16 минут в течение года, нужна для точного часа восхода
    и заката, не только приблизительного."""
    L = math.radians(sun_longitude(jd))
    return 229.18 * (0.000075 + 0.001868 * math.cos(L) - 0.032077 * math.sin(L)
                      - 0.014615 * math.cos(2 * L) - 0.040849 * math.sin(2 * L))


def sunrise_sunset_utc_hours(jd_local_midnight, lat, lon):
    """Восход и закат Солнца, в часах UTC от полуночи, для конкретных
    суток (начало суток, jd_local_midnight, JD в 00:00 UTC того дня)
    и координат места. Учитывает наклон эклиптики и уравнение времени."""
    jd_noon_approx = jd_local_midnight + 0.5 - lon / 360
    decl = math.degrees(math.asin(math.sin(math.radians(23.44)) * math.sin(math.radians(sun_longitude(jd_noon_approx)))))
    lat_r, decl_r = math.radians(lat), math.radians(decl)
    cos_h = (math.sin(math.radians(-0.833)) - math.sin(lat_r) * math.sin(decl_r)) / (math.cos(lat_r) * math.cos(decl_r))
    cos_h = max(-1, min(1, cos_h))
    H = math.degrees(math.acos(cos_h))
    eot = equation_of_time_minutes(jd_noon_approx) / 60
    solar_noon_utc = 12 - lon / 15 - eot
    return solar_noon_utc - H / 15, solar_noon_utc + H / 15


CHALDEAN_ORDER = ["saturn", "jupiter", "mars", "sun", "venus", "mercury", "moon"]
WEEKDAY_RULER = {0: "moon", 1: "mars", 2: "mercury", 3: "jupiter", 4: "venus", 5: "saturn", 6: "sun"}
# Python: понедельник=0 ... воскресенье=6


def planetary_hours(jd_local_midnight, lat, lon, tz_offset, weekday):
    """24 планетных часа (западная система): 12 дневных от восхода до
    заката и 12 ночных от заката до следующего восхода, каждый час
    правит одна из семи классических планет по кругу Халдеев, начиная
    с планеты-управителя дня недели. weekday передаётся явно, 0=понедельник
    ... 6=воскресенье, как в datetime.weekday(), чтобы не путать его с
    часовым поясом при выводе через смещённый JD. Возвращает список из 24
    словарей с планетой, началом и концом часа в часах локального времени."""
    sunrise_utc, sunset_utc = sunrise_sunset_utc_hours(jd_local_midnight, lat, lon)
    next_sunrise_utc, _ = sunrise_sunset_utc_hours(jd_local_midnight + 1, lat, lon)
    next_sunrise_utc += 24

    ruler = WEEKDAY_RULER[weekday]
    start_idx = CHALDEAN_ORDER.index(ruler)

    day_step = (sunset_utc - sunrise_utc) / 12
    night_step = (next_sunrise_utc - sunset_utc) / 12

    hours = []
    for i in range(12):
        planet = CHALDEAN_ORDER[(start_idx + i) % 7]
        hours.append({"planet": planet, "start": sunrise_utc + i * day_step + tz_offset,
                       "end": sunrise_utc + (i + 1) * day_step + tz_offset, "is_day": True})
    for i in range(12):
        planet = CHALDEAN_ORDER[(start_idx + 12 + i) % 7]
        hours.append({"planet": planet, "start": sunset_utc + i * night_step + tz_offset,
                       "end": sunset_utc + (i + 1) * night_step + tz_offset, "is_day": False})
    return hours


CHOGHADIYA_NAMES = ["udveg", "chal", "labh", "amrit", "kaal", "shubh", "rog"]
CHOGHADIYA_OF_PLANET = {"sun": "udveg", "venus": "chal", "mercury": "labh", "moon": "amrit",
                         "saturn": "kaal", "jupiter": "shubh", "mars": "rog"}


def choghadiya(jd_local_midnight, lat, lon, tz_offset, weekday):
    """8 дневных и 8 ночных чогхадий: каждый день делится на восемь равных
    частей от восхода до заката, и ещё восемь от заката до следующего
    восхода. Первая дневная часть определяется планетой-управителем дня
    недели (weekday передаётся явно, см. planetary_hours), дальше идёт
    круг Халдеев. Ночная часть начинается на пять шагов вперёд по тому же
    кругу от дневного старта, отдельное традиционное правило, не
    продолжение дневного счёта."""
    sunrise_utc, sunset_utc = sunrise_sunset_utc_hours(jd_local_midnight, lat, lon)
    next_sunrise_utc, _ = sunrise_sunset_utc_hours(jd_local_midnight + 1, lat, lon)
    next_sunrise_utc += 24

    ruler = WEEKDAY_RULER[weekday]
    day_start_idx = CHALDEAN_ORDER.index(ruler)
    night_start_idx = (day_start_idx + 5) % 7

    day_step = (sunset_utc - sunrise_utc) / 8
    night_step = (next_sunrise_utc - sunset_utc) / 8

    slots = []
    for i in range(8):
        planet = CHALDEAN_ORDER[(day_start_idx + i) % 7]
        slots.append({"name": CHOGHADIYA_OF_PLANET[planet], "start": sunrise_utc + i * day_step + tz_offset,
                       "end": sunrise_utc + (i + 1) * day_step + tz_offset, "is_day": True})
    for i in range(8):
        planet = CHALDEAN_ORDER[(night_start_idx + i) % 7]
        slots.append({"name": CHOGHADIYA_OF_PLANET[planet], "start": sunset_utc + i * night_step + tz_offset,
                       "end": sunset_utc + (i + 1) * night_step + tz_offset, "is_day": False})
    return slots


def find_solar_return_jd(natal_sun_lon, year, birth_month, birth_day):
    """Точный момент солнечного возвращения (соляра): когда транзитное
    Солнце в указанном году встаёт ровно на натальную долготу. Ищет
    методом последовательного приближения от дня рождения в этом году,
    сходится за несколько шагов, Солнце движется ~0.9856° в сутки."""
    guess_jd = to_jd(year, birth_month, birth_day, 12, 0)
    for _ in range(12):
        current_lon = sun_longitude(guess_jd)
        diff = norm360(natal_sun_lon - current_lon)
        if diff > 180:
            diff -= 360
        guess_jd += diff / 0.9856
    return guess_jd


def personal_year_number(day, month, current_year):
    """Число года: день и месяц рождения плюс текущий год, свёрнутые в одну
    цифру от 1 до 9. В отличие от числа жизненного пути, здесь мастер-числа
    традиционно не сохраняются, сворачивается до конца."""
    digits = [int(c) for c in f"{day:02d}{month:02d}{current_year:04d}"]
    total = sum(digits)
    while total > 9:
        total = sum(int(c) for c in str(total))
    return total


def karmic_debt_number(day):
    """Кармический долг по традиции смотрит на сам день рождения: если это
    13, 14, 16 или 19 любого месяца, число считается значимым. Есть не у
    всех, возвращает None, если дня нет в этом списке."""
    return day if day in (13, 14, 16, 19) else None


def _pinnacle_reduce(n):
    while n > 9 and n not in (11, 22, 33):
        n = sum(int(c) for c in str(n))
    return n


TRADITIONAL_RULER = {
    "Овен": "mars", "Телец": "venus", "Близнецы": "mercury", "Рак": "moon",
    "Лев": "sun", "Дева": "mercury", "Весы": "venus", "Скорпион": "mars",
    "Стрелец": "jupiter", "Козерог": "saturn", "Водолей": "saturn", "Рыбы": "jupiter",
}


def money_planet(asc_sign_name):
    """Денежная планета: управитель знака на втором доме (дом денег),
    по классическим, домодерным управителям семи планет (Скорпион и
    Водолей через Марс и Сатурн, не через Плутон и Уран), чтобы
    согласоваться с остальной системой семи планет в боте."""
    second_house_sign = sign_in_house(asc_sign_name, 2)
    return TRADITIONAL_RULER[second_house_sign]


def pinnacles(day, month, year):
    """Четыре вершины (pinnacle numbers): периоды жизни, у каждого своё
    число и свои возрастные границы, посчитанные от даты рождения. Первая
    вершина длится от рождения до возраста (36 минус базовое число
    жизненного пути, мастер-числа берутся в свёрнутом виде только для этого
    расчёта возраста), дальше по девять лет на вторую и третью, четвёртая
    длится до конца жизни. Возвращает список из четырёх словарей с числом,
    возрастом начала и возрастом конца (None для последней)."""
    m = _pinnacle_reduce(month)
    d = _pinnacle_reduce(day)
    y = _pinnacle_reduce(sum(int(c) for c in str(year)))
    p1 = _pinnacle_reduce(m + d)
    p2 = _pinnacle_reduce(d + y)
    p3 = _pinnacle_reduce(p1 + p2)
    p4 = _pinnacle_reduce(m + y)

    lp = life_path_number(day, month, year)
    base = lp if lp not in (11, 22, 33) else {11: 2, 22: 4, 33: 6}[lp]
    end1 = 36 - base
    end2 = end1 + 9
    end3 = end2 + 9

    return [
        {"number": p1, "start_age": 0, "end_age": end1},
        {"number": p2, "start_age": end1, "end_age": end2},
        {"number": p3, "start_age": end2, "end_age": end3},
        {"number": p4, "start_age": end3, "end_age": None},
    ]


NAKSHATRAS = [
    "Ашвини", "Бхарани", "Криттика", "Рохини", "Мригашира", "Ардра", "Пунарвасу",
    "Пушья", "Ашлеша", "Магха", "Пурва Пхалгуни", "Уттара Пхалгуни", "Хаста",
    "Читра", "Свати", "Вишакха", "Анурадха", "Джйештха", "Мула", "Пурва Ашадха",
    "Уттара Ашадха", "Шравана", "Дхаништха", "Шатабхиша", "Пурва Бхадрапада",
    "Уттара Бхадрапада", "Ревати",
]

# традиционная классификация накшатр по гане (темперамент) и нади (тип энергии),
# по 9 накшатр на каждую из 3 категорий в обеих системах
_GANA_CYCLE = ["deva","manushya","rakshasa","manushya","deva","manushya","deva","deva","rakshasa",
               "rakshasa","manushya","manushya","deva","rakshasa","deva","rakshasa","deva","rakshasa",
               "rakshasa","manushya","manushya","deva","rakshasa","rakshasa","manushya","manushya","deva"]
_NADI_CYCLE = ["adi","madhya","antya","antya","madhya","adi","adi","madhya","antya",
               "antya","madhya","adi","adi","madhya","antya","antya","madhya","adi",
               "adi","madhya","antya","antya","madhya","adi","adi","madhya","antya"]
GANA_OF_NAKSHATRA = dict(zip(NAKSHATRAS, _GANA_CYCLE))
NADI_OF_NAKSHATRA = dict(zip(NAKSHATRAS, _NADI_CYCLE))


def lahiri_ayanamsa(jd):
    """Айянамша Лахири: угол между тропическим и сидерическим зодиаком,
    растёт из-за прецессии равноденствий примерно на 50.24 угл. секунды в
    год. Опорная точка J2000.0 = 23.8531°, что совпадает с общепринятым
    значением для ведической астрологии."""
    T = (jd - 2451545.0) / 36525
    years_from_2000 = T * 100
    return 23.8531 + years_from_2000 * (50.2388475 / 3600)


def sidereal_lon(tropical_lon, jd):
    return norm360(tropical_lon - lahiri_ayanamsa(jd))


def nakshatra_of(sidereal_moon_lon):
    span = 360 / 27
    idx = int(sidereal_moon_lon // span)
    pada = int((sidereal_moon_lon % span) // (span / 4)) + 1
    return {"name": NAKSHATRAS[idx], "pada": pada}


# ---------- Виmшоттари-даша: ведическая система периодов планет ----------
DASHA_YEARS = {"ketu": 7, "venus": 20, "sun": 6, "moon": 10, "mars": 7,
               "rahu": 18, "jupiter": 16, "saturn": 19, "mercury": 17}
DASHA_ORDER = ["ketu", "venus", "sun", "moon", "mars", "rahu", "jupiter", "saturn", "mercury"]
DASHA_YEAR_DAYS = 365.25


def dasha_timeline(birth_jd, moon_sidereal_lon, years_ahead=120):
    """Хронология махадаш (больших периодов) системы Вимшоттари от рождения.
    Первую дашу и её управителя даёт накшатра Луны при рождении: у каждой
    накшатры есть управитель по фиксированному циклу из девяти планет
    (индекс накшатры по модулю 9). Первая даша всегда неполная, остаток
    считается по тому, сколько градусов накшатры Луна уже прошла к моменту
    рождения. Дальше периоды идут полными, по кругу, в одном и том же
    порядке планет, пока не наберётся нужное количество лет вперёд."""
    nak_span = 360 / 27
    nak_index = int(moon_sidereal_lon // nak_span)
    position_in_nak = moon_sidereal_lon % nak_span
    fraction_elapsed = position_in_nak / nak_span

    start_idx = nak_index % 9
    first_planet = DASHA_ORDER[start_idx]
    first_balance_years = DASHA_YEARS[first_planet] * (1 - fraction_elapsed)

    timeline = []
    cursor_jd = birth_jd
    end_jd = cursor_jd + first_balance_years * DASHA_YEAR_DAYS
    timeline.append((first_planet, cursor_jd, end_jd))
    cursor_jd = end_jd

    idx = (start_idx + 1) % 9
    while cursor_jd - birth_jd < years_ahead * DASHA_YEAR_DAYS:
        planet = DASHA_ORDER[idx]
        end_jd = cursor_jd + DASHA_YEARS[planet] * DASHA_YEAR_DAYS
        timeline.append((planet, cursor_jd, end_jd))
        cursor_jd = end_jd
        idx = (idx + 1) % 9

    return timeline


def current_dasha(timeline, now_jd):
    for planet, start_jd, end_jd in timeline:
        if start_jd <= now_jd < end_jd:
            return planet, start_jd, end_jd
    return None


def point_lon(point):
    """Долгота 0-360 из словаря {'sign':..., 'deg':...}."""
    return SIGNS.index(point["sign"]) * 30 + point["deg"]


def natal_aspect(lon1, lon2):
    """Тип угла между двумя фиксированными долготами (натальная синастрия,
    не транзит): соединение / трин (и секстиль вместе с ним, как лёгкая
    гармония) / квадрат / оппозиция / без значимого угла."""
    diff = abs(norm360(lon1 - lon2))
    if diff > 180:
        diff = 360 - diff
    if diff <= 8:
        return "conjunction"
    if abs(diff - 60) <= 6 or abs(diff - 120) <= 8:
        return "trine"
    if abs(diff - 90) <= 7:
        return "square"
    if abs(diff - 180) <= 8:
        return "opposition"
    return "none"


def chart_balance(chart):
    """Считает баланс стихий (огонь/земля/воздух/вода) и качеств
    (кардинальность/фиксированность/мутабельность) по восьми точкам карты.
    Возвращает доминирующие категории (может быть несколько при ничьей)."""
    points = ["sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn", "rising"]
    element_counts = {"fire": 0, "earth": 0, "air": 0, "water": 0}
    modality_counts = {"cardinal": 0, "fixed": 0, "mutable": 0}
    for key in points:
        p = chart.get(key)
        if not p:
            continue
        sign = p["sign"]
        element_counts[ELEMENT_OF_SIGN[sign]] += 1
        modality_counts[MODALITY_OF_SIGN[sign]] += 1
    max_element = max(element_counts.values())
    max_modality = max(modality_counts.values())
    dominant_elements = [k for k, v in element_counts.items() if v == max_element]
    dominant_modalities = [k for k, v in modality_counts.items() if v == max_modality]
    return {
        "element_counts": element_counts,
        "modality_counts": modality_counts,
        "dominant_elements": dominant_elements,
        "dominant_modalities": dominant_modalities,
    }


def axis_word(n):
    n_mod = abs(n) % 100
    n1 = n_mod % 10
    if 11 <= n_mod <= 14:
        return "осей"
    if n1 == 1:
        return "ось"
    if 2 <= n1 <= 4:
        return "оси"
    return "осей"


def year_word(n):
    n = abs(n) % 100
    n1 = n % 10
    if 11 <= n <= 14:
        return "лет"
    if n1 == 1:
        return "год"
    if 2 <= n1 <= 4:
        return "года"
    return "лет"


def jd_to_date_label(jd):
    year, month, day = jd_to_ymd(jd)
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
    # Стандартные кеплеровы элементы JPL (эпоха J2000, диапазон 1800-2050),
    # тот же источник и та же точность, что уже используется для Марса/Юпитера/Сатурна выше.
    "uranus":  dict(a=19.18916464, e=0.04725744, L0=313.23810451, Lr=428.48202785, pi0=170.95427630, pir=0.40805281),
    "neptune": dict(a=30.06992276, e=0.00859048, L0=304.87997031, Lr=218.45945325, pi0=44.96476227, pir=-0.32241464),
    "pluto":   dict(a=39.48211675, e=0.24882730, L0=238.92903833, Lr=145.20780515, pi0=224.06891629, pir=-0.04062942),
}


def lilith_mean_longitude(jd):
    """Средняя (не истинная) Чёрная Луна — Лилит, точка среднего апогея
    лунной орбиты. Формула Meeus, цикл ~8.85 года, движение прямое."""
    t = (jd - 2451545.0) / 36525
    lon = (83.3532465 + 4069.0137287 * t - 0.0103200 * t * t
           - t ** 3 / 80053.0 + t ** 4 / 18999000.0)
    return norm360(lon)


def vertex_longitude(jd, lat_deg, lon_deg):
    """Вертекс — точка пересечения эклиптики с первым вертикалом (западная
    сторона). Считается той же формулой, что и асцендент (см. ascendant()
    выше), но с местным звёздным временем +90° и с ко-широтой (90°-широта)
    вместо самой широты, плюс разворот на 180° к западной точке."""
    lst = norm360(gst(jd) + lon_deg + 90)
    lst_rad = math.radians(lst)
    eps = math.radians(obliquity(jd))
    colat = math.radians(90 - lat_deg)
    y = math.cos(lst_rad)
    x = -(math.sin(eps) * math.tan(colat) + math.cos(eps) * math.sin(lst_rad))
    return norm360(math.degrees(math.atan2(y, x)) + 180)


def part_of_fortune_longitude(asc_lon, sun_lon, moon_lon, is_day_chart):
    """Парс Фортуны: для дневной карты Asc+Луна-Солнце, для ночной
    Asc+Солнце-Луна (классическая, не гелленистическая антифортуна)."""
    if is_day_chart:
        return norm360(asc_lon + moon_lon - sun_lon)
    return norm360(asc_lon + sun_lon - moon_lon)


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


def north_node_longitude(jd):
    """Средний Северный узел Луны. Формула проверена: полный цикл ~18.6 года
    (ретроградно), совпадает с известными датами смены знака в 2022-2027 гг."""
    t = (jd - 2451545.0) / 36525
    omega = 125.04452 - 1934.136261 * t + 0.0020708 * t * t + t ** 3 / 450000
    return norm360(omega)


def compute_chart(date_str, time_str, city_label):
    y, m, d = (int(x) for x in date_str.split("-"))
    has_time = bool(time_str)
    hour, minute = 12, 0
    if has_time:
        hh, mm = (int(x) for x in time_str.split(":"))
        hour, minute = hh, mm

    city_match = geocode_city(city_label)
    used_default_city = city_match is None
    if city_match:
        city = {"lat": city_match["lat"], "lon": city_match["lon"], "label": city_match["name"], "tz": city_match["tz"]}
        tz_offset = utc_offset_hours(city_match["tz"], y, m, d, hour, minute)
    else:
        city = {"lat": 55.7558, "lon": 37.6173, "label": "Москва", "tz": "Europe/Moscow"}
        tz_offset = utc_offset_hours("Europe/Moscow", y, m, d, hour, minute)

    utc_hour_float = hour - tz_offset
    utc_hour = int(utc_hour_float)
    utc_minute = minute + round((utc_hour_float - utc_hour) * 60)
    jd = to_jd(y, m, d, utc_hour, utc_minute)

    north_node_lon = north_node_longitude(jd)
    chart = {
        "sun": sign_of(sun_longitude(jd)),
        "moon": sign_of(moon_longitude(jd)),
        "mercury": sign_of(planet_longitude("mercury", jd)),
        "venus": sign_of(planet_longitude("venus", jd)),
        "mars": sign_of(planet_longitude("mars", jd)),
        "jupiter": sign_of(planet_longitude("jupiter", jd)),
        "saturn": sign_of(planet_longitude("saturn", jd)),
        "rising": sign_of(ascendant(jd, city["lat"], city["lon"])) if has_time else None,
        "north_node": sign_of(north_node_lon),
        "south_node": sign_of(north_node_lon + 180),
        "north_node_lon": north_node_lon,
        "birth_jd": jd,
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
                h["transit_sign"] = sign_of(planet_longitude(planet_key, now_jd + h["day_offset"]))["sign"]
                results.append(h)
    results.sort(key=lambda h: h["day_offset"])
    return results


def house_of_sign(sign_name, asc_sign_name):
    idx = SIGNS.index(sign_name)
    asc_idx = SIGNS.index(asc_sign_name)
    return ((idx - asc_idx) % 12) + 1


def sign_in_house(asc_sign_name, house_num):
    asc_idx = SIGNS.index(asc_sign_name)
    return SIGNS[(asc_idx + house_num - 1) % 12]


def find_house_ingresses(planet_key, target_house, asc_sign_name, start_jd, window_days=730):
    """Находит даты, когда транзитная планета входит в конкретный дом карты.
    Это не привязано к аспекту с натальной точкой, поэтому событий заметно
    больше и они регулярнее, чем в find_transit_hits: подходит для вопроса
    "когда именно эта сфера жизни активна", а не только "когда точный аспект"."""
    houses = []
    for t in range(window_days + 1):
        sign = sign_of(planet_longitude(planet_key, start_jd + t))["sign"]
        houses.append(house_of_sign(sign, asc_sign_name))
    ingresses = []
    for t in range(1, window_days + 1):
        if houses[t] == target_house and houses[t - 1] != target_house:
            ingresses.append(t)
    return ingresses


def is_retrograde(planet_key, jd):
    """True, если планета в этот момент движется попятно (ретроградно):
    долгота через день меньше, чем сейчас, с учётом перехода через 360°."""
    lon_now = planet_longitude(planet_key, jd)
    lon_next = planet_longitude(planet_key, jd + 1)
    diff = norm360(lon_next - lon_now)
    return diff > 180


# ---------- расширенная карта: внешние планеты, Лилит/Вертекс/Парс, дома ----------

EXTENDED_PLANET_KEYS = ["sun", "moon", "mercury", "venus", "mars", "jupiter",
                        "saturn", "uranus", "neptune", "pluto"]
POINT_KEYS = ["lilith", "vertex", "fortune"]


def compute_extended_chart(chart):
    """Достраивает базовую карту (см. compute_chart) до полного набора:
    Уран/Нептун/Плутон, Лилит/Вертекс/Парс Фортуны, плюс номер дома для
    каждой из 13 точек (whole-sign дома от асцендента, как и house_of_sign
    в остальном коде). Дома считаются, только если известно время рождения
    (нужен настоящий асцендент), иначе house=None у всех точек."""
    jd = chart["birth_jd"]
    city = chart["city"]
    has_time = chart["has_time"] and chart.get("rising") is not None

    sun_lon = sun_longitude(jd)
    moon_lon = moon_longitude(jd)

    ext = dict(chart)
    ext["uranus"] = sign_of(planet_longitude("uranus", jd))
    ext["neptune"] = sign_of(planet_longitude("neptune", jd))
    ext["pluto"] = sign_of(planet_longitude("pluto", jd))
    ext["lilith"] = sign_of(lilith_mean_longitude(jd))

    if has_time:
        asc_lon = ascendant(jd, city["lat"], city["lon"])
        ext["vertex"] = sign_of(vertex_longitude(jd, city["lat"], city["lon"]))
        asc_sign_idx = SIGNS.index(chart["rising"]["sign"])
        sun_house = ((SIGNS.index(sign_of(sun_lon)["sign"]) - asc_sign_idx) % 12) + 1
        is_day = 7 <= sun_house <= 12
        ext["fortune"] = sign_of(part_of_fortune_longitude(asc_lon, sun_lon, moon_lon, is_day))
        houses = {}
        for key in EXTENDED_PLANET_KEYS + POINT_KEYS:
            houses[key] = house_of_sign(ext[key]["sign"], chart["rising"]["sign"])
        ext["houses"] = houses
    else:
        ext["vertex"] = None
        ext["fortune"] = None
        ext["houses"] = None

    return ext


ASPECT_DEFS = [
    ("conjunction", 0, 8),
    ("sextile", 60, 6),
    ("square", 90, 7),
    ("trine", 120, 8),
    ("opposition", 180, 8),
]
HARMONY_WEIGHT = {"conjunction": 0.3, "sextile": 1.0, "square": -1.0, "trine": 1.2, "opposition": -1.0}


def full_aspect(lon1, lon2):
    """Тип аспекта и точный орб (в отличие от natal_aspect(), который только
    классифицирует). Возвращает (kind, orb, max_orb) либо (None, None, None),
    если между точками нет значимого аспекта."""
    diff = ang_diff(lon1, lon2)
    for kind, angle, max_orb in ASPECT_DEFS:
        if abs(diff - angle) <= max_orb:
            return kind, abs(diff - angle), max_orb
    return None, None, None


def harmony_score(ext_chart):
    """Балл гармонии карты — собственный метод в духе космодинов Эдвала
    (не воспроизводит его точную формулу, она не опубликована, но использует
    тот же принцип: сила аспекта тем больше, чем точнее орб, гармоничные
    аспекты в плюс, напряжённые в минус). Считает по 10 классическим планетам.
    Возвращает total (float), planets_in_harmony (int, сколько планет чаще
    в гармоничных, чем в напряжённых аспектах) и разбивку по парам."""
    lons = {}
    for key in EXTENDED_PLANET_KEYS:
        p = ext_chart[key]
        lons[key] = SIGNS.index(p["sign"]) * 30 + p["deg"]

    pair_scores = []
    per_planet = {k: 0.0 for k in EXTENDED_PLANET_KEYS}
    for i, k1 in enumerate(EXTENDED_PLANET_KEYS):
        for k2 in EXTENDED_PLANET_KEYS[i + 1:]:
            kind, orb, max_orb = full_aspect(lons[k1], lons[k2])
            if kind is None:
                continue
            closeness = 1 - (orb / max_orb)
            weight = HARMONY_WEIGHT[kind] * closeness
            pair_scores.append({"a": k1, "b": k2, "kind": kind, "orb": round(orb, 2), "weight": round(weight, 2)})
            per_planet[k1] += weight
            per_planet[k2] += weight

    total = sum(p["weight"] for p in pair_scores)
    planets_in_harmony = sum(1 for v in per_planet.values() if v > 0)
    return {
        "total": round(total, 2),
        "planets_in_harmony": planets_in_harmony,
        "per_planet": {k: round(v, 2) for k, v in per_planet.items()},
        "pairs": pair_scores,
    }


# ---------- астрокартография: меридиан, где точка карты была в кульминации при рождении ----------

def mc_line_longitude(ecliptic_lon_deg, jd):
    """Долгота меридиана (линии MC), на котором данная точка эклиптики
    находилась точно в верхней кульминации (на MC) в момент jd. Реальный
    астрономический расчёт: точка на этом меридиане в момент рождения
    стояла в зените неба, а не абстрактная метафора."""
    eps = math.radians(obliquity(jd))
    lam = math.radians(norm360(ecliptic_lon_deg))
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam)))
    ra = norm360(ra)
    lon = norm360(ra - gst(jd))
    if lon > 180:
        lon -= 360
    return lon


ASTROCARTO_CITIES = [
    ("Рейкьявиком", -21.9), ("Лиссабоном", -9.1), ("Касабланкой", -7.6), ("Лондоном", -0.1),
    ("Мадридом", -3.7), ("Парижем", 2.3), ("Римом", 12.5), ("Берлином", 13.4), ("Осло", 10.7),
    ("Веной", 16.4), ("Стокгольмом", 18.1), ("Варшавой", 21.0), ("Афинами", 23.7),
    ("Хельсинки", 24.9), ("Каиром", 31.2), ("Стамбулом", 28.9), ("Киевом", 30.5),
    ("Санкт-Петербургом", 30.3), ("Иерусалимом", 35.2), ("Москвой", 37.6), ("Найроби", 36.8),
    ("Багдадом", 44.4), ("Тегераном", 51.4), ("Дубаем", 55.3), ("Эр-Риядом", 46.7),
    ("Кейптауном", 18.4), ("Йоханнесбургом", 28.0), ("Лагосом", 3.4), ("Екатеринбургом", 60.6),
    ("Ташкентом", 69.2), ("Мумбаи", 72.9), ("Дели", 77.2), ("Калькуттой", 88.4),
    ("Бангкоком", 100.5), ("Сингапуром", 103.8), ("Джакартой", 106.8), ("Улан-Батором", 106.9),
    ("Пекином", 116.4), ("Гонконгом", 114.2), ("Шанхаем", 121.5), ("Манилой", 121.0),
    ("Сеулом", 127.0), ("Владивостоком", 131.9), ("Токио", 139.7), ("Пертом", 115.9),
    ("Мельбурном", 144.9), ("Сиднеем", 151.2), ("Оклендом", 174.8), ("Гонолулу", -157.9),
    ("Анкориджем", -149.9), ("Ванкувером", -123.1), ("Лос-Анджелесом", -118.2),
    ("Сан-Франциско", -122.4), ("Денвером", -104.9), ("Мехико", -99.1), ("Чикаго", -87.6),
    ("Майами", -80.2), ("Торонто", -79.4), ("Нью-Йорком", -74.0), ("Боготой", -74.1),
    ("Лимой", -77.0), ("Сантьяго", -70.6), ("Буэнос-Айресом", -58.4), ("Сан-Паулу", -46.6),
    ("Рио-де-Жанейро", -43.2),
]


def nearest_astrocarto_cities(lon_deg, n=2):
    def circ_dist(a, b):
        d = abs(a - b) % 360
        return min(d, 360 - d)
    ranked = sorted(ASTROCARTO_CITIES, key=lambda c: circ_dist(c[1], lon_deg))
    return [name for name, _ in ranked[:n]]
