"""Рендер колеса натальной карты — PNG для платного расширенного разбора.

Тот же фирменный стиль, что и у спидометра гармонии (gauge.py): тёмно-синий
фон, золото, но здесь ещё и цветные линии аспектов между планетами, как на
референсных скриншотах eso3.com.

Ориентация — стандартная для астрологического софта: асцендент слева (9 часов),
знаки идут против часовой стрелки. Без известного времени рождения (когда
асцендента нет) Овен 0° условно ставится в ту же точку слева.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

BG = (11, 16, 38)          # #0b1026
BG_ALT = (16, 22, 48)       # чуть светлее — для чередования секторов знаков
GOLD = (232, 200, 138)      # #e8c88a
WHITE = (240, 240, 245)
GREY = (120, 128, 150)
RING_LINE = (60, 68, 96)

ASPECT_COLOR = {
    "trine": (90, 168, 110),        # зелёный — гармония
    "sextile": (108, 160, 214),     # голубой — гармония
    "square": (196, 64, 64),        # красный — напряжение
    "opposition": (214, 130, 74),   # оранжевый — напряжение
    "conjunction": (232, 200, 138), # золото — нейтрально/усиление
}

ZODIAC_GLYPH = ["♈", "♉", "♊", "♋", "♌", "♍", "♎", "♏", "♐", "♑", "♒", "♓"]
PLANET_GLYPH = {
    "sun": "☉", "moon": "☽", "mercury": "☿", "venus": "♀", "mars": "♂",
    "jupiter": "♃", "saturn": "♄", "uranus": "♅", "neptune": "♆", "pluto": "♇",
}

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")


def _font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(os.path.join(_FONTS_DIR, name), size)


def _norm360(x):
    x = x % 360
    return x + 360 if x < 0 else x


def _lon_to_angle(lon, asc_lon):
    """Эклиптическая долгота -> угол PIL (0°=восток, по часовой стрелке).
    Асцендент всегда оказывается на 180° (запад/9 часов), знаки идут против
    часовой стрелки при возрастании долготы — стандартная ориентация чартов."""
    return _norm360(180 - _norm360(lon - asc_lon))


def _polar(cx, cy, r, angle_deg):
    rad = math.radians(angle_deg)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


def _centered_text(draw, cx, top_y, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (bbox[2] - bbox[0]) / 2, top_y), text, font=font, fill=fill)


def render_natal_wheel(planet_lons, asc_lon, aspect_pairs, out_path, size=1000, has_houses=True):
    """planet_lons: {"sun": долгота0-360, ...} для 10 классических планет.
    asc_lon: долгота асцендента (0, если время рождения неизвестно).
    aspect_pairs: список словарей {"a","b","kind"} из astro_calc.harmony_score()["pairs"].
    has_houses: рисовать ли номера домов (нужен реальный асцендент)."""
    img = Image.new("RGB", (size, size), BG)
    draw = ImageDraw.Draw(img)
    cx = cy = size // 2

    r_zodiac_out = int(size * 0.47)
    r_zodiac_in = int(size * 0.40)
    r_house_label = int(size * 0.36)
    r_planet = int(size * 0.30)
    r_center = int(size * 0.20)

    # сектора знаков зодиака, чередующиеся по цвету
    for i in range(12):
        lon_lo, lon_hi = i * 30, i * 30 + 30
        a_lo = _lon_to_angle(lon_lo, asc_lon)
        a_hi = _lon_to_angle(lon_hi, asc_lon)
        if a_hi > a_lo:
            a_hi -= 360
        color = BG_ALT if i % 2 == 0 else BG
        draw.pieslice([cx - r_zodiac_out, cy - r_zodiac_out, cx + r_zodiac_out, cy + r_zodiac_out],
                      start=a_hi, end=a_lo, fill=color, outline=RING_LINE, width=2)
    draw.ellipse([cx - r_zodiac_in, cy - r_zodiac_in, cx + r_zodiac_in, cy + r_zodiac_in], fill=BG, outline=RING_LINE, width=2)

    asc_sign_idx = int(_norm360(asc_lon) // 30)

    zodiac_font = _font(30)
    house_font = _font(20)
    for i in range(12):
        mid_lon = i * 30 + 15
        angle = _lon_to_angle(mid_lon, asc_lon)
        gx, gy = _polar(cx, cy, (r_zodiac_out + r_zodiac_in) / 2, angle)
        bbox = draw.textbbox((0, 0), ZODIAC_GLYPH[i], font=zodiac_font)
        draw.text((gx - (bbox[2] - bbox[0]) / 2, gy - (bbox[3] - bbox[1]) / 2 - bbox[1]), ZODIAC_GLYPH[i], font=zodiac_font, fill=GOLD)

        if has_houses:
            house_num = ((i - asc_sign_idx) % 12) + 1
            hx, hy = _polar(cx, cy, r_house_label, angle)
            label = str(house_num)
            bbox = draw.textbbox((0, 0), label, font=house_font)
            draw.text((hx - (bbox[2] - bbox[0]) / 2, hy - (bbox[3] - bbox[1]) / 2 - bbox[1]), label, font=house_font, fill=GREY)

    # оси асцендент/десцендент, условные MC/IC (только если известно время)
    if has_houses:
        for offset in (0, 90, 180, 270):
            angle = _lon_to_angle(asc_lon + offset, asc_lon)
            x1, y1 = _polar(cx, cy, r_center, angle)
            x2, y2 = _polar(cx, cy, r_zodiac_in, angle)
            draw.line([x1, y1, x2, y2], fill=GOLD, width=2)

    # позиции планет (с лёгким разводом при кластеризации близких долгот)
    order = sorted(planet_lons.keys(), key=lambda k: planet_lons[k])
    placed_radius = {}
    last_lon = None
    step = 0
    for key in order:
        lon = planet_lons[key]
        if last_lon is not None and abs(_norm360(lon - last_lon)) < 7:
            step += 1
        else:
            step = 0
        placed_radius[key] = r_planet - step * 26
        last_lon = lon

    positions = {}
    for key, lon in planet_lons.items():
        angle = _lon_to_angle(lon, asc_lon)
        positions[key] = _polar(cx, cy, placed_radius[key], angle)

    # линии аспектов — под планетами, чтобы не перекрывали символы
    for pair in aspect_pairs:
        a, b, kind = pair["a"], pair["b"], pair["kind"]
        if a not in positions or b not in positions:
            continue
        color = ASPECT_COLOR.get(kind)
        if not color:
            continue
        x1, y1 = positions[a]
        x2, y2 = positions[b]
        width = 3 if kind in ("trine", "square") else 2
        draw.line([x1, y1, x2, y2], fill=color, width=width)

    # символы планет поверх линий
    planet_font = _font(38, bold=True)
    for key, (px, py) in positions.items():
        glyph = PLANET_GLYPH[key]
        bbox = draw.textbbox((0, 0), glyph, font=planet_font)
        gw, gh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = 6
        draw.ellipse([px - gw / 2 - pad, py - gh / 2 - pad - bbox[1], px + gw / 2 + pad, py + gh / 2 + pad - bbox[1]], fill=BG)
        draw.text((px - gw / 2, py - gh / 2 - bbox[1]), glyph, font=planet_font, fill=WHITE)

    title_font = _font(30, bold=True)
    _centered_text(draw, cx, 14, "Ваша натальная карта", title_font, GOLD)

    img.save(out_path)
    return out_path
