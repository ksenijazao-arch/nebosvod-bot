"""
Небосвод, бот для индивидуального натального разбора.

Запуск:
    pip install -r requirements.txt
    export BOT_TOKEN="ваш_токен_от_BotFather"
    python bot.py

Как это работает:
    /start -> пол -> дата рождения -> время (можно пропустить) -> город (можно пропустить)
    -> бот присылает разбор по восьми точкам карты
    -> кнопка "Узнать важные даты" присылает прогноз на два года по Юпитеру и Сатурну
"""
import asyncio
import logging
import os
import re
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg2

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

import astro_calc as ac
import content as ct

DATABASE_URL = os.environ.get("DATABASE_URL")
BROADCAST_PASSWORD = os.environ.get("BROADCAST_PASSWORD", "")


def db_connect():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def db_init():
    if not DATABASE_URL:
        return
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS users (chat_id BIGINT PRIMARY KEY, first_seen TIMESTAMPTZ DEFAULT now())")
        conn.commit()


def db_remember_user(chat_id):
    if not DATABASE_URL:
        return
    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO users (chat_id) VALUES (%s) ON CONFLICT DO NOTHING", (chat_id,))
            conn.commit()
    except Exception:
        logging.exception("не удалось сохранить пользователя")


def db_all_users():
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT chat_id FROM users")
        return [row[0] for row in cur.fetchall()]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("nebosvod")

ASK_GENDER, ASK_DATE, ASK_TIME, ASK_CITY = range(4)

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]

GENDER_CB_PREFIX = "gender:"
SKIP_TIME_CB = "skip_time"
SKIP_CITY_CB = "skip_city"
FORECAST_CB = "forecast"
RESTART_CB = "restart"
SPHERE_CB_PREFIX = "sphere:"
UNLIVED_CB = "unlived"


def fmt_date(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year}"


async def typing_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, seconds: float):
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    await asyncio.sleep(seconds)


async def send_bot(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, delay: float = 1.0, **kwargs):
    await typing_delay(context, chat_id, delay)
    text = ct.personalize_text(text, context.user_data.get("gender"))
    await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)


def gender_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Мужчина", callback_data=f"{GENDER_CB_PREFIX}male"),
        InlineKeyboardButton("Женщина", callback_data=f"{GENDER_CB_PREFIX}female"),
    ]])


async def ask_birth_date(context, chat_id: int) -> int:
    await send_bot(
        context, chat_id,
        "Теперь дата рождения. Пришлите её в формате ДД.ММ.ГГГГ — например, 20.08.1993.",
        0.7,
    )
    return ASK_DATE


# ---------- разговор: сбор даты, времени, города ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    chat_id = update.effective_chat.id
    db_remember_user(chat_id)
    avatar_path = os.path.join(os.path.dirname(__file__), "avatar.png")
    if os.path.exists(avatar_path):
        with open(avatar_path, "rb") as f:
            await context.bot.send_photo(chat_id=chat_id, photo=f)
    await send_bot(
        context, chat_id,
        "Здравствуйте! Я «Небосвод». Соберу натальную карту и разберу её в нескольких слоях: планеты, дома, важные периоды и общий баланс карты.",
        0.6,
    )
    await send_bot(
        context, chat_id,
        "Сначала уточню пол — он нужен только для корректных русских формулировок в тексте разбора.",
        0.5,
        reply_markup=gender_keyboard(),
    )
    return ASK_GENDER


async def got_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    value = query.data[len(GENDER_CB_PREFIX):]
    if value not in {"male", "female"}:
        return ASK_GENDER
    context.user_data["gender"] = value
    await query.edit_message_reply_markup(reply_markup=None)
    return await ask_birth_date(context, query.message.chat_id)


async def got_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", text)
    if not m:
        await update.message.reply_text("Не разобрала дату. Пришлите в формате ДД.ММ.ГГГГ, например 20.08.1993.")
        return ASK_DATE
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        date(year, month, day)
    except ValueError:
        await update.message.reply_text("Такой даты не существует. Проверьте число и месяц, и пришлите ещё раз.")
        return ASK_DATE

    context.user_data["date_str"] = f"{year:04d}-{month:02d}-{day:02d}"
    chat_id = update.effective_chat.id

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Не знаю точно", callback_data=SKIP_TIME_CB)]])
    await send_bot(
        context, chat_id,
        "Хорошо. А время рождения знаете? Чем точнее время, тем точнее выйдет асцендент.\n\n"
        "Пришлите время в формате ЧЧ:ММ или ЧЧ.ММ (например 14:15 или 14.15), или нажмите кнопку ниже, если не знаете.",
        0.8, reply_markup=keyboard,
    )
    return ASK_TIME


async def got_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    m = re.match(r"^(\d{1,2})[:.,](\d{2})$", text)
    if not m or not (0 <= int(m.group(1)) <= 23) or not (0 <= int(m.group(2)) <= 59):
        await update.message.reply_text("Не разобрала время. Напишите часы и минуты через двоеточие или точку, например 14:15 или 14.15, или нажмите «Не знаю точно» выше.")
        return ASK_TIME
    context.user_data["time_str"] = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    return await ask_city(update, context)


async def skip_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["time_str"] = ""
    await query.edit_message_reply_markup(reply_markup=None)
    return await ask_city(update, context, chat_id=query.message.chat_id)


async def ask_city(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int = None) -> int:
    chat_id = chat_id or update.effective_chat.id
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить", callback_data=SKIP_CITY_CB)]])
    await send_bot(
        context, chat_id,
        "И последнее: город рождения, он нужен для расчёта асцендента. Пришлите название города.",
        0.8, reply_markup=keyboard,
    )
    return ASK_CITY


async def got_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["city"] = update.message.text.strip()
    return await deliver_chart(update, context, chat_id=update.effective_chat.id)


async def skip_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["city"] = ""
    await query.edit_message_reply_markup(reply_markup=None)
    return await deliver_chart(update, context, chat_id=query.message.chat_id)


# ---------- сборка и отправка разбора ----------

SPHERE_EMOJI = {"money": "💰", "love": "❤️", "career": "💼", "health": "🌿"}


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{SPHERE_EMOJI[key]} {s['title']}", callback_data=f"{SPHERE_CB_PREFIX}{key}") for key, s in list(ct.SPHERES.items())[:2]],
        [InlineKeyboardButton(f"{SPHERE_EMOJI[key]} {s['title']}", callback_data=f"{SPHERE_CB_PREFIX}{key}") for key, s in list(ct.SPHERES.items())[2:]],
        [InlineKeyboardButton("🔭 Узнать важные даты", callback_data=FORECAST_CB)],
        [InlineKeyboardButton("✨ Непрожитые жизни", callback_data=UNLIVED_CB)],
        [InlineKeyboardButton("🔄 Начать заново", callback_data=RESTART_CB)],
    ])


async def send_menu(context, chat_id):
    await context.bot.send_message(chat_id=chat_id, text="Что дальше?", reply_markup=main_menu_keyboard())


async def deliver_chart(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> int:
    await send_bot(context, chat_id, "Секунду, считаю положение светил на момент вашего рождения…", 1.1)

    chart = ac.compute_chart(
        context.user_data["date_str"],
        context.user_data.get("time_str", ""),
        context.user_data.get("city", ""),
    )
    context.user_data["chart"] = chart

    await send_bot(context, chat_id, "🌙 Карта готова. Сначала — большая тройка.", 0.6)

    await send_bot(
        context, chat_id,
        f"☀️ Солнце в знаке {ac.SIGN_GENITIVE[chart['sun']['sign']]}.\n\n{ct.SUN_TEXTS[chart['sun']['sign']]}",
        1.3,
    )
    await send_bot(
        context, chat_id,
        f"🌙 Луна {ac.v_predlog(chart['moon']['sign'])} {ac.SIGN_PREPOSITIONAL[chart['moon']['sign']]}.\n\n{ct.MOON_TEXTS[chart['moon']['sign']]}",
        1.0,
    )
    if chart["has_time"]:
        note = " (город не указан, расчёт приблизительный, по Москве)" if chart["used_default_city"] else ""
        await send_bot(
            context, chat_id,
            f"↗️ Асцендент: {chart['rising']['sign']}{note}.\n\n{ct.RISING_TEXTS[chart['rising']['sign']]}",
            1.0,
        )
    else:
        await send_bot(
            context, chat_id,
            "Без точного времени рождения асцендент посчитать нельзя: он меняется примерно раз в два часа. "
            "Вернитесь с точным временем, и я его найду.",
            1.0,
        )

    await send_bot(context, chat_id, "🪐 Теперь — личные планеты и две опорные социальные планеты.", 0.9)

    await send_bot(
        context, chat_id,
        f"Меркурий {ac.v_predlog(chart['mercury']['sign'])} {ac.SIGN_PREPOSITIONAL[chart['mercury']['sign']]}. {ct.MERCURY_TEXTS[chart['mercury']['sign']]}\n\n"
        f"Венера {ac.v_predlog(chart['venus']['sign'])} {ac.SIGN_PREPOSITIONAL[chart['venus']['sign']]}. {ct.VENUS_TEXTS[chart['venus']['sign']]}",
        1.4,
    )
    await send_bot(
        context, chat_id,
        f"Марс {ac.v_predlog(chart['mars']['sign'])} {ac.SIGN_PREPOSITIONAL[chart['mars']['sign']]}. {ct.MARS_TEXTS[chart['mars']['sign']]}\n\n"
        f"Юпитер {ac.v_predlog(chart['jupiter']['sign'])} {ac.SIGN_PREPOSITIONAL[chart['jupiter']['sign']]}. {ct.JUPITER_TEXTS[chart['jupiter']['sign']]}",
        1.4,
    )
    await send_bot(
        context, chat_id,
        f"Сатурн {ac.v_predlog(chart['saturn']['sign'])} {ac.SIGN_PREPOSITIONAL[chart['saturn']['sign']]}. {ct.SATURN_TEXTS[chart['saturn']['sign']]}",
        1.2,
    )

    balance = ac.chart_balance(chart)
    element_profiles = [ct.ELEMENT_PROFILES[key] for key in balance["dominant_elements"]]
    modality_profiles = [ct.MODALITY_PROFILES[key] for key in balance["dominant_modalities"]]
    element_names = " + ".join(item[0] for item in element_profiles)
    element_text = " ".join(item[1] for item in element_profiles)
    modality_names = " + ".join(item[0] for item in modality_profiles)
    modality_text = " ".join(item[1] for item in modality_profiles)
    element_label = "Ведущая стихия" if len(element_profiles) == 1 else "Баланс ведущих стихий"
    modality_label = "Ведущий тип действия" if len(modality_profiles) == 1 else "Баланс типов действия"
    await send_bot(
        context, chat_id,
        f"🧭 Второй слой — баланс карты.\n\n"
        f"{element_label}: {element_names}. {element_text}\n\n"
        f"{modality_label}: {modality_names}. {modality_text}",
        1.3,
    )

    await send_menu(context, chat_id)
    return ConversationHandler.END


# ---------- сферы жизни ----------

def _group_ingress_hits(raw_hits):
    """Склеивает повторные входы одной планеты в один период и выбирает разные сюжеты."""
    by_planet = {}
    for hit in sorted(raw_hits, key=lambda h: h["day_offset"]):
        groups = by_planet.setdefault(hit["planet_key"], [])
        if groups and hit["day_offset"] - groups[-1][-1]["day_offset"] <= 150:
            groups[-1].append(hit)
        else:
            groups.append([hit])

    grouped = []
    for planet_key, groups in by_planet.items():
        for idx, group in enumerate(groups):
            grouped.append({
                "planet_key": planet_key,
                "start_day": group[0]["day_offset"],
                "start_label": group[0]["date_label"],
                "end_label": group[-1]["date_label"],
                "returns": len(group) > 1,
                "retrograde": any(x.get("retrograde") for x in group),
                "variant": idx % 4,
            })
    grouped.sort(key=lambda h: h["start_day"])

    # Сначала берём разные планеты, затем — повторные сюжеты, если места остались.
    selected, used = [], set()
    for hit in grouped:
        if hit["planet_key"] not in used:
            selected.append(hit)
            used.add(hit["planet_key"])
        if len(selected) == 4:
            return selected
    for hit in grouped:
        if hit not in selected:
            selected.append(hit)
        if len(selected) == 4:
            break
    return selected


def _period_label(hit):
    if hit["start_label"] == hit["end_label"]:
        return hit["start_label"]
    return f"{hit['start_label']} — {hit['end_label']}"


async def sphere(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    sphere_key = query.data[len(SPHERE_CB_PREFIX):]
    s = ct.SPHERES.get(sphere_key)
    chart = context.user_data.get("chart")
    if not s or not chart:
        await context.bot.send_message(chat_id=chat_id, text="Сначала пройдите разбор заново: /start")
        return

    await send_bot(context, chat_id, f"{SPHERE_EMOJI[sphere_key]} Разбираю сферу «{s['title']}» в двух слоях: базовый сценарий и ближайшие периоды.", 0.7)

    planet_sign = chart[s["planet_key"]]["sign"]
    if chart["has_time"] and chart["rising"]:
        house_sign = ac.sign_in_house(chart["rising"]["sign"], s["house_num"])
        await send_bot(
            context, chat_id,
            f"Главный сценарий\n\n{s['house_texts'][house_sign]}\n\nПрактический фокус: {s['advice'][house_sign]}",
            1.4,
        )

        cache_key = f"house_ingress_{s['house_num']}"
        ingress_hits = context.user_data.get(cache_key)
        if ingress_hits is None:
            now = ac.to_jd(date.today().year, date.today().month, date.today().day, 0, 0)
            raw_hits = []
            for planet_key in ["mercury", "venus", "mars", "jupiter", "saturn"]:
                for t in ac.find_house_ingresses(planet_key, s["house_num"], chart["rising"]["sign"], now, 730):
                    raw_hits.append({
                        "planet_key": planet_key,
                        "day_offset": t,
                        "date_label": ac.jd_to_date_label(now + t),
                        "retrograde": ac.is_retrograde(planet_key, now + t),
                    })
            ingress_hits = _group_ingress_hits(raw_hits)
            context.user_data[cache_key] = ingress_hits

        if ingress_hits:
            await send_bot(context, chat_id, "🔭 Ближайшие окна. Я склеила повторные ретроградные входы, чтобы один и тот же сюжет не дублировался несколькими почти одинаковыми датами.", 0.7)
            for h in ingress_hits:
                variants = ct.SPHERE_EVENT_TEXTS[h["planet_key"]][sphere_key]
                text = variants[h["variant"] % len(variants)]
                return_note = " Тема может вернуться второй волной — это часть одного периода, а не отдельное новое событие." if h["returns"] else ""
                await send_bot(
                    context, chat_id,
                    f"📅 {_period_label(h)} · {ct.PLANET_LABEL[h['planet_key']]}\n"
                    f"Фокус: {ct.PLANET_REASON[h['planet_key']]}.\n\n{text}{return_note}\n\n"
                    f"{ct.PLANET_WEIGHT[h['planet_key']]}",
                    1.05,
                )
        else:
            await send_bot(
                context, chat_id,
                f"В ближайшие два года крупных входов планет в дом сферы «{s['title']}» не нашлось. Это не значит, что тема стоит на месте: просто этот конкретный индикатор сейчас не даёт отдельного окна.",
                0.9,
            )
    else:
        await send_bot(
            context, chat_id,
            f"Без точного времени рождения дома карты ненадёжны, поэтому я не буду придумывать даты по этой сфере. Но планетарный слой остаётся: {s['planet_texts'][planet_sign]}",
            1.2,
        )
    await send_menu(context, chat_id)


# ---------- сканер непрожитых жизней ----------

async def unlived(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    chart = context.user_data.get("chart")
    if not chart:
        await context.bot.send_message(chat_id=chat_id, text="Сначала пройдите разбор заново: /start")
        return

    if not (chart["has_time"] and chart["rising"]):
        await send_bot(
            context, chat_id,
            "Для этого раздела нужен дом Северного узла, а дом без точного времени рождения ненадёжен. Если знаете время, начните разбор заново и укажите его.",
            1.0,
        )
        return

    await send_bot(
        context, chat_id,
        "✨ «Непрожитые жизни» — это не буквальная параллельная судьба. Я использую ось Южного и Северного узлов как способ описать привычный ресурс и направление, которое человек часто откладывает.",
        1.0,
    )

    house_num = ac.house_of_sign(chart["north_node"]["sign"], chart["rising"]["sign"])
    south_sign = chart["south_node"]["sign"]
    north_sign = chart["north_node"]["sign"]
    saturn_sentence = f"Сатурн в {ac.SIGN_PREPOSITIONAL[chart['saturn']['sign']]} добавляет масштаб: вы способны {ct.SATURN_MAGNITUDE[chart['saturn']['sign']]}."
    potential = ct.HOUSE_POTENTIAL[str(house_num)].format(saturn=saturn_sentence)

    await send_bot(
        context, chat_id,
        f"1/4 · Что уже развито\n\nЮжный узел в {ac.SIGN_PREPOSITIONAL[south_sign]}: вы уже умеете {ct.SOUTH_NODE_RESOURCES[south_sign]}. Это не нужно ломать — это ваш готовый инструмент.",
        1.5,
    )
    await send_bot(
        context, chat_id,
        f"2/4 · Куда растёт карта\n\nСеверный узел в {ac.SIGN_PREPOSITIONAL[north_sign]}, дом {house_num}. {ct.NODE_VISIONS[north_sign]}\n\n{potential}",
        2.0,
    )
    await send_bot(
        context, chat_id,
        f"3/4 · Где обычно возникает сопротивление\n\n{ct.HOUSE_SHADOW[str(house_num)]}\n\nВопрос для проверки на реальной жизни: {ct.HOUSE_REFLECTION[str(house_num)]}\n\n{ct.VALIDATION[str(house_num)]}",
        1.8,
    )
    await send_bot(
        context, chat_id,
        f"4/4 · Как вернуть эту линию в жизнь\n\nБольшой шаг: {ct.HOUSE_RETURN_ADVICE[str(house_num)]}\n\nМалый эксперимент: {ct.HOUSE_RETURN_ADVICE_2[str(house_num)]}",
        1.8,
    )

    now = ac.to_jd(date.today().year, date.today().month, date.today().day, 0, 0)
    birth_jd = chart["birth_jd"]
    lifespan_days = int(now - birth_jd)
    if lifespan_days > 0:
        past_hits = ac.find_transit_hits(chart["north_node_lon"], "saturn", birth_jd, lifespan_days)
        major = [h for h in past_hits if h["aspect_key"] in ("conjunction", "opposition")]
        if major:
            last = major[-1]
            event_jd = birth_jd + last["day_offset"]
            ey, em, _ = ac.jd_to_ymd(event_jd)
            by, bm, bd = ac.jd_to_ymd(birth_jd)
            age = ey - by - (1 if (em, 1) < (bm, bd) else 0)
            month_year = f"{ct.MONTHS_PREP[em - 1]} {ey}"
            template = ct.NODE_SATURN_PAST[last["aspect_key"]]
            await send_bot(
                context, chat_id,
                template.format(age=age, year_word=ac.year_word(age), month_year=month_year, tie=ct.HOUSE_INFO[house_num]["tie"]),
                2.0,
            )

        future_hits = ac.find_transit_hits(chart["north_node_lon"], "saturn", now, 6570)
        future_major = [h for h in future_hits if h["aspect_key"] in ("conjunction", "opposition")]
        if future_major:
            nxt = future_major[0]
            event_jd = now + nxt["day_offset"]
            ey, em, _ = ac.jd_to_ymd(event_jd)
            by, bm, bd = ac.jd_to_ymd(birth_jd)
            age = ey - by - (1 if (em, 1) < (bm, bd) else 0)
            month_year = f"{ct.MONTHS_PREP[em - 1]} {ey}"
            template = ct.NODE_SATURN_FUTURE[nxt["aspect_key"]]
            await send_bot(
                context, chat_id,
                template.format(age=age, year_word=ac.year_word(age), month_year=month_year, tie=ct.HOUSE_INFO[house_num]["tie"]),
                1.8,
            )

    for planet_key in ["venus", "mars", "mercury", "jupiter", "saturn"]:
        if ac.is_retrograde(planet_key, birth_jd):
            await send_bot(context, chat_id, "↩️ Дополнительный слой: " + ct.RETRO_NARRATIVES[planet_key], 1.8)
            break

    await send_menu(context, chat_id)


# ---------- прогноз ----------

async def forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    chart = context.user_data.get("chart")
    if not chart:
        await context.bot.send_message(chat_id=chat_id, text="Сначала пройдите разбор заново: /start")
        return

    await send_bot(context, chat_id,
                    "Смотрю, где транзитный Юпитер и Сатурн подходят к вашим Солнцу и Луне в ближайшие два года…",
                    1.2)

    now = ac.to_jd(date.today().year, date.today().month, date.today().day, 0, 0)
    hits = context.user_data.get("forecast")
    if hits is None:
        hits = ac.compute_forecast(chart, now, window_days=730)
        context.user_data["forecast"] = hits

    if not hits:
        await send_bot(
            context, chat_id,
            "В ближайшие два года Юпитер и Сатурн не образуют точных аспектов к вашим Солнцу и Луне. "
            "Это не значит, что ничего не происходит, просто главные переключатели сейчас не здесь. "
            "Более полная картина потребует остальных планет и домов.",
            1.2,
        )
    else:
        for h in hits[:6]:
            text = ct.TRANSIT_TEXTS[h["planet_key"]][h["point_key"]][h["aspect_key"]]
            house_note = ""
            if chart["has_time"] and chart["rising"]:
                house_num = ac.house_of_sign(h["transit_sign"], chart["rising"]["sign"])
                info = ct.HOUSE_INFO[house_num]
                house_note = (f" Сейчас {ct.PLANET_LABEL[h['planet_key']]} идёт через ваш {info['label']}, "
                              f"и заметнее всего это отразится на {info['tie']}.")
            await send_bot(
                context, chat_id,
                f"{h['date_label']}: {ct.PLANET_LABEL[h['planet_key']]} образует {ct.ASPECT_ACCUSATIVE[h['aspect_key']]} "
                f"с {ct.NATAL_INSTRUMENTAL[h['point_key']]}. {text}.{house_note}",
                0.95,
            )
        if len(hits) > 6:
            await send_bot(context, chat_id,
                            f"Это первые 6 дат из {len(hits)} найденных на два года вперёд, остальные дальше по времени.",
                            0.7)

    await send_menu(context, chat_id)


async def restart_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    chat_id = query.message.chat_id
    await send_bot(context, chat_id, "Начинаем заново. Сначала уточню пол для корректных формулировок.", 0.5, reply_markup=gender_keyboard())
    return ASK_GENDER


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Хорошо, остановились. Наберите /start, когда захотите начать заново.",
                                     reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Nebosvod bot is running")

    def log_message(self, format, *args):
        pass  # не засоряем логи проверками на живость


def start_health_server():
    """Render (и похожие площадки) ждут, что сервис слушает порт.
    Сам бот работает через постоянный опрос Telegram и порт не использует,
    так что здесь просто открываем его для проверки, что сервис жив."""
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("health-сервер слушает порт %s", port)


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text or ""
    parts = text.split(maxsplit=2)
    if len(parts) < 3 or parts[1] != BROADCAST_PASSWORD or not BROADCAST_PASSWORD:
        await update.message.reply_text("Формат: /broadcast пароль текст сообщения")
        return
    message = parts[2]
    users = db_all_users()
    sent, failed = 0, 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=message)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"Разослано: {sent}, не доставлено: {failed}, всего в базе: {len(users)}.")


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("Задайте переменную окружения BOT_TOKEN с токеном от @BotFather.")

    start_health_server()
    db_init()

    application = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start), CallbackQueryHandler(restart_button, pattern=f"^{RESTART_CB}$")],
        states={
            ASK_GENDER: [CallbackQueryHandler(got_gender, pattern=f"^{GENDER_CB_PREFIX}(male|female)$")],
            ASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_date)],
            ASK_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_time),
                CallbackQueryHandler(skip_time, pattern=f"^{SKIP_TIME_CB}$"),
            ],
            ASK_CITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_city),
                CallbackQueryHandler(skip_city, pattern=f"^{SKIP_CITY_CB}$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv)
    application.add_handler(CallbackQueryHandler(forecast, pattern=f"^{FORECAST_CB}$"))
    application.add_handler(CallbackQueryHandler(sphere, pattern=f"^{SPHERE_CB_PREFIX}"))
    application.add_handler(CallbackQueryHandler(unlived, pattern=f"^{UNLIVED_CB}$"))
    application.add_handler(CommandHandler("broadcast", broadcast))

    log.info("Небосвод запущен, жду сообщений…")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
