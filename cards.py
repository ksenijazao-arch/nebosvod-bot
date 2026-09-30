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


def _fit_font(draw, text, max_w, start, bold=True, min_size=60):
    size = start
    while size > min_size:
        f = _font(size, bold)
        b = draw.textbbox((0, 0), text, font=f)
        if b[2] - b[0] <= max_w:
            return f
        size -= 4
    return _font(min_size, bold)


def render_vedic_story(sun_west, sun_vedic, moon_vedic, nak_name, pada, out_path, glyphs=None):
    """Вертикальная карточка 1080x1920 для сторис после бесплатного разбора:
    накшатра Луны, ведические Солнце и Луна, западное Солнце и адрес бота.
    Человек сохраняет её и выкладывает, подруги видят бота."""
    import random
    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    rnd = random.Random(hash(nak_name) & 0xFFFF)
    for _ in range(170):
        x, y, r = rnd.randint(0, W), rnd.randint(0, H), rnd.choice([1, 1, 1, 2, 2, 3])
        col = GOLD if rnd.random() < 0.18 else (200, 204, 220)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=col)
    draw.rounded_rectangle([44, 44, W - 44, H - 44], radius=36, outline=GOLD, width=3)

    cx = W // 2
    g = glyphs or {}
    _centered(draw, cx, 150, "Н Е Б О С В О Д", _font(34, True), GOLD)

    # полумесяц
    mx, my, R = cx, 420, 120
    draw.ellipse([mx - R, my - R, mx + R, my + R], fill=GOLD)
    draw.ellipse([mx - R + 58, my - R - 18, mx + R + 58, my + R - 18], fill=BG)

    _centered(draw, cx, 640, "МОЯ НАКШАТРА", _font(40, True), GREY)
    nf = _fit_font(draw, nak_name, W - 180, 128)
    _centered(draw, cx, 720, nak_name, nf, GOLD)
    _centered(draw, cx, 900, f"{pada}-я пада · лунная стоянка", _font(40), WHITE)

    draw.line([cx - 150, 1010, cx + 150, 1010], fill=GOLD, width=3)

    rows = [("Солнце в джйотиш", sun_vedic), ("Луна в джйотиш", moon_vedic), ("Солнце на Западе", sun_west)]
    y = 1080
    lab_f, val_f = _font(38), _font(50, True)
    for label, sign in rows:
        _centered(draw, cx, y, label, lab_f, GREY)
        _centered(draw, cx, y + 58, f"{g.get(sign, '')} {sign}".strip(), val_f, WHITE)
        y += 170

    _centered(draw, cx, 1640, "А какая накшатра у тебя?", _font(44, True), GOLD)
    _centered(draw, cx, 1720, "@nebosvod_astro_bot", _font(40), WHITE)
    img.save(out_path)
    return out_path
