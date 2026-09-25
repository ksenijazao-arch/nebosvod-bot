"""Карточка одной планеты/точки для расширенного разбора — PNG, отправляется
перед текстом по каждой из 13 позиций. Тот же фирменный стиль, что у
спидометра и колеса (gauge.py, wheel.py): тёмно-синий фон, золото, плюс
цветной акцент — зелёный, если планета в среднем в гармоничных аспектах с
остальными, красный — если в напряжённых.
"""
import os

from PIL import Image, ImageDraw, ImageFont

BG = (11, 16, 38)
GOLD = (232, 200, 138)
WHITE = (240, 240, 245)
GREEN = (90, 168, 110)
RED = (196, 92, 92)
GREY = (140, 148, 168)

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")


def _font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(os.path.join(_FONTS_DIR, name), size)


def _centered(draw, cx, top_y, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (bbox[2] - bbox[0]) / 2, top_y - bbox[1]), text, font=font, fill=fill)
    return bbox[3] - bbox[1]


def render_point_card(glyph, name, sign_glyph, sign_name, house_num, harmony_weight, out_path, size=640):
    """glyph — символ планеты/точки, name — подпись ('Солнце', 'Лилит'...),
    sign_glyph/sign_name — знак, house_num — номер дома или None,
    harmony_weight — суммарный вес аспектов этой точки (для цвета акцента,
    None — если не считается, как у Лилит/Вертекса/Парса)."""
    img = Image.new("RGB", (size, size), BG)
    draw = ImageDraw.Draw(img)
    cx = size // 2

    if harmony_weight is None:
        accent = GOLD
    elif harmony_weight > 0:
        accent = GREEN
    else:
        accent = RED

    draw.rounded_rectangle([16, 16, size - 16, size - 16], radius=28, outline=accent, width=4)

    glyph_font = _font(180)
    _centered(draw, cx, 60, glyph, glyph_font, WHITE)

    name_font = _font(46, bold=True)
    _centered(draw, cx, 270, name.upper(), name_font, GOLD)

    sign_font = _font(38)
    sign_line = f"{sign_glyph} {sign_name}"
    _centered(draw, cx, 340, sign_line, sign_font, WHITE)

    if house_num is not None:
        house_font = _font(30)
        _centered(draw, cx, 400, f"{house_num} дом", house_font, GREY)

    brand_font = _font(22)
    _centered(draw, cx, size - 50, "@nebosvod_astro_bot", brand_font, GREY)

    img.save(out_path)
    return out_path
