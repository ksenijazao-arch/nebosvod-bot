"""Рендер спидометра гармонии натальной карты — PNG-картинка для бота.

Стиль: тёмно-синий фон (#0b1026) и золотой акцент (#e8c88a), те же цвета,
что уже используются в оформлении Instagram-аккаунта проекта, чтобы бот и
соцсети выглядели одним брендом. Геометрия — классический полукруглый
спидометр (купол сверху, стрелка от нижнего центра), как в референсе.

Система углов — PIL-овская (0° = восток/право, отсчёт по часовой стрелке,
т.к. ось Y направлена вниз): 180°=запад/лево, 270°=север/верх, 360°=восток/право.
Купол сверху = дуга от 180° до 360°, проходящая через 270° (верх).
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

BG = (11, 16, 38)          # #0b1026
GOLD = (232, 200, 138)     # #e8c88a
WHITE = (240, 240, 245)
RED = (196, 64, 64)
YELLOW = (214, 178, 74)
GREEN = (90, 168, 110)
NEEDLE = (232, 200, 138)

SCORE_MIN = -10.0
SCORE_MAX = 10.0

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")


def _font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(os.path.join(_FONTS_DIR, name), size)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _value_to_angle(value, lo, hi):
    """MIN -> 180° (запад/лево), MAX -> 360° (восток/право), дуга через верх."""
    frac = _clamp((value - lo) / (hi - lo), 0, 1)
    return 180 + frac * 180


def _polar(cx, cy, r, angle_deg):
    rad = math.radians(angle_deg)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


def _centered_text(draw, cx, top_y, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (bbox[2] - bbox[0]) / 2, top_y), text, font=font, fill=fill)


def render_harmony_gauge(score, planets_in_harmony, out_path, width=900, height=700):
    """Рисует спидометр гармонии и сохраняет PNG по out_path."""
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    cx = width // 2
    cy = int(height * 0.68)          # нижний центр — точка вращения стрелки
    r_outer = int(width * 0.40)
    r_inner = int(width * 0.29)

    zones = [
        (SCORE_MIN, -3.0, RED),
        (-3.0, 3.0, YELLOW),
        (3.0, SCORE_MAX, GREEN),
    ]
    for lo, hi, color in zones:
        a_start = _value_to_angle(lo, SCORE_MIN, SCORE_MAX)
        a_end = _value_to_angle(hi, SCORE_MIN, SCORE_MAX)
        draw.pieslice(
            [cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer],
            start=a_start, end=a_end, fill=color,
        )
    draw.pieslice([cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner], 0, 360, fill=BG)

    tick_font = _font(22)
    for v in range(int(SCORE_MIN), int(SCORE_MAX) + 1, 2):
        angle = _value_to_angle(v, SCORE_MIN, SCORE_MAX)
        x1, y1 = _polar(cx, cy, r_inner - 4, angle)
        x2, y2 = _polar(cx, cy, r_outer + 4, angle)
        draw.line([x1, y1, x2, y2], fill=BG, width=4)
        lx, ly = _polar(cx, cy, r_outer + 34, angle)
        label = str(v)
        bbox = draw.textbbox((0, 0), label, font=tick_font)
        draw.text((lx - (bbox[2] - bbox[0]) / 2, ly - (bbox[3] - bbox[1]) / 2), label, font=tick_font, fill=WHITE)

    clamped_score = _clamp(score, SCORE_MIN, SCORE_MAX)
    needle_angle = _value_to_angle(clamped_score, SCORE_MIN, SCORE_MAX)
    needle_len = r_outer - 20
    nx, ny = _polar(cx, cy, needle_len, needle_angle)
    perp = needle_angle + 90
    base_w = 9
    bx1, by1 = _polar(cx, cy, base_w, perp)
    bx2, by2 = _polar(cx, cy, base_w, perp + 180)
    draw.polygon([(bx1, by1), (nx, ny), (bx2, by2)], fill=NEEDLE)
    hub_r = 15
    draw.ellipse([cx - hub_r, cy - hub_r, cx + hub_r, cy + hub_r], fill=GOLD)

    score_font = _font(58, bold=True)
    _centered_text(draw, cx, cy + 36, f"{score:+.1f}", score_font, WHITE)

    title_font = _font(32, bold=True)
    _centered_text(draw, width / 2, 28, "Насколько гармонична ваша карта", title_font, GOLD)

    sub_font = _font(24)
    _centered_text(draw, width / 2, height - 46, f"Планет в гармоничных аспектах: {planets_in_harmony} из 10", sub_font, WHITE)

    img.save(out_path)
    return out_path
