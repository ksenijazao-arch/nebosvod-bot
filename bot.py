"""
Небосвод, бот для индивидуального натального разбора.

Запуск:
    pip install -r requirements.txt
    export BOT_TOKEN="ваш_токен_от_BotFather"
    python bot.py

Как это работает:
    /start -> дата рождения -> время (можно пропустить) -> город (можно пропустить)
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

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

import astro_calc as ac
import content as ct

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("nebosvod")

ASK_DATE, ASK_TIME, ASK_CITY = range(3)

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]

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
    await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)


# ---------- разговор: сбор даты, времени, города ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    chat_id = update.effective_chat.id
    avatar_path = os.path.join(os.path.dirname(__file__), "avatar.png")
    if os.path.exists(avatar_path):
        with open(avatar_path, "rb") as f:
            await context.bot.send_photo(chat_id=chat_id, photo=f)
    await send_bot(context, chat_id,
                    "Здравствуйте! Я «Небосвод», бот для индивидуального разбора натальной карты.", 0.6)
    await send_bot(context, chat_id,
                    "Чтобы найти ваше Солнце, Луну и ещё шесть точек карты, мне нужны дата, время и город рождения.\n\n"
                    "Начнём с даты. Пришлите её в формате ДД.ММ.ГГГГ, например 20.08.1993.", 0.8)
    return ASK_DATE


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

    await send_bot(context, chat_id, "Готово. Начнём с большой тройки.", 0.6)

    await send_bot(
        context, chat_id,
        f"Ваше Солнце в знаке {ac.SIGN_GENITIVE[chart['sun']['sign']]}.\n\n{ct.SUN_TEXTS[chart['sun']['sign']]}",
        1.3,
    )
    await send_bot(
        context, chat_id,
        f"Луна {ac.v_predlog(chart['moon']['sign'])} {ac.SIGN_PREPOSITIONAL[chart['moon']['sign']]}. {ct.MOON_TEXTS[chart['moon']['sign']]}",
        1.0,
    )
    if chart["has_time"]:
        note = " (город не указан, расчёт приблизительный, по Москве)" if chart["used_default_city"] else ""
        await send_bot(
            context, chat_id,
            f"Восходящий знак: {chart['rising']['sign']}{note}. {ct.RISING_TEXTS[chart['rising']['sign']]}",
            1.0,
        )
    else:
        await send_bot(
            context, chat_id,
            "Без точного времени рождения асцендент посчитать нельзя: он меняется примерно раз в два часа. "
            "Вернитесь с точным временем, и я его найду.",
            1.0,
        )

    await send_bot(context, chat_id, "Это только три точки из восьми. Идём глубже.", 0.9)

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

    await send_menu(context, chat_id)
    return ConversationHandler.END


# ---------- сферы жизни ----------

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

    await send_bot(context, chat_id, f"Смотрю, что карта говорит про {s['title'].lower()}…", 0.7)

    planet_sign = chart[s["planet_key"]]["sign"]
    if chart["has_time"] and chart["rising"]:
        house_sign = ac.sign_in_house(chart["rising"]["sign"], s["house_num"])
        await send_bot(context, chat_id, f"{s['house_texts'][house_sign]}\n\nЧто с этим делать: {s['advice'][house_sign]}", 1.4)

        cache_key = f"house_ingress_{s['house_num']}"
        ingress_hits = context.user_data.get(cache_key)
        if ingress_hits is None:
            now = ac.to_jd(date.today().year, date.today().month, date.today().day, 0, 0)
            raw_hits = []
            for planet_key in ["mercury", "venus", "mars", "jupiter", "saturn"]:
                for t in ac.find_house_ingresses(planet_key, s["house_num"], chart["rising"]["sign"], now, 730):
                    raw_hits.append({
                        "planet_key": planet_key, "day_offset": t,
                        "date_label": ac.jd_to_date_label(now + t),
                        "retrograde": ac.is_retrograde(planet_key, now + t),
                    })
            raw_hits.sort(key=lambda h: h["day_offset"])
            seen_count = {}
            for h in raw_hits:
                idx = seen_count.get(h["planet_key"], 0)
                h["variant"] = idx % 2
                seen_count[h["planet_key"]] = idx + 1
            ingress_hits = raw_hits
            context.user_data[cache_key] = ingress_hits

        if ingress_hits:
            lines = ["🔭 Ближайшие даты, когда эта сфера особенно активна:"]
            for h in ingress_hits[:5]:
                bank = ct.PLANET_SPHERE_ACTION if h["variant"] == 0 else ct.PLANET_SPHERE_ACTION_V2
                action = bank[h["planet_key"]][sphere_key]
                if h["retrograde"]:
                    lead = f"{ct.PLANET_LABEL[h['planet_key']]} сейчас здесь, но движется попятно"
                else:
                    lead = ct.PLANET_REASON[h["planet_key"]]
                lines.append(f"\n📅 {h['date_label']}. {lead}: {action}. {ct.PLANET_WEIGHT[h['planet_key']]}")
            await send_bot(context, chat_id, "\n".join(lines), 1.6)
        else:
            await send_bot(
                context, chat_id,
                f"По сфере «{s['title']}» на ближайшие два года дат не находится, это редкое, но возможное совпадение.",
                0.9,
            )
    else:
        await send_bot(
            context, chat_id,
            f"Без точного времени рождения не вижу, в каком доме у вас сейчас {s['title'].lower()}, "
            f"но кое-что скажу и так. {s['planet_texts'][planet_sign]}",
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
            "Этот сканер смотрит на дом Северного узла в вашей карте, а для домов нужно точное время рождения. "
            "Начните разбор заново и укажите время, тогда я смогу его включить.",
            1.0,
        )
        return

    await send_bot(context, chat_id, "Смотрю на узлы и Сатурн в вашей карте…", 1.0)

    house_num = ac.house_of_sign(chart["north_node"]["sign"], chart["rising"]["sign"])
    saturn_sentence = f"Ваш Сатурн в {ac.SIGN_PREPOSITIONAL[chart['saturn']['sign']]} показывает, что вы способны {ct.SATURN_MAGNITUDE[chart['saturn']['sign']]}."
    potential = ct.HOUSE_POTENTIAL[str(house_num)].format(saturn=saturn_sentence)
    vision = ct.NODE_VISIONS[chart["north_node"]["sign"]]
    validation = ct.VALIDATION[str(house_num)]
    advice1 = ct.HOUSE_RETURN_ADVICE[str(house_num)]
    advice2 = ct.HOUSE_RETURN_ADVICE_2[str(house_num)]

    await send_bot(
        context, chat_id,
        "У каждого в карте есть путь, которым вы прошли, и путь, который звал, но остался в стороне. "
        f"Вот что я вижу в вашем случае.\n\n{potential}",
        2.2,
    )
    await send_bot(context, chat_id, f"А если бы вы тогда выбрали иначе: {vision}\n\n{validation}", 2.0)
    await send_bot(context, chat_id, f"✅ Что можно сделать уже сейчас:\n1. {advice1}\n2. {advice2}", 1.6)

    now = ac.to_jd(date.today().year, date.today().month, date.today().day, 0, 0)
    power_hits = []
    for planet_key in ["mercury", "venus", "mars", "jupiter", "saturn"]:
        for t in ac.find_house_ingresses(planet_key, house_num, chart["rising"]["sign"], now, 730):
            power_hits.append({"planet_key": planet_key, "day_offset": t, "date_label": ac.jd_to_date_label(now + t)})
    power_hits.sort(key=lambda h: h["day_offset"])

    if power_hits:
        h = power_hits[0]
        tie = ct.HOUSE_INFO[house_num]["tie"]
        await send_bot(
            context, chat_id,
            f"🌟 И ещё одно: 📅 {h['date_label']}. {ct.PLANET_REASON[h['planet_key']]}, прямо там, где живёт этот нереализованный путь. "
            f"Особенно легко в этот день повлиять на {tie}. {ct.PLANET_WEIGHT[h['planet_key']]}",
            1.4,
        )
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
    await send_bot(context, chat_id,
                    "Здравствуйте! Я «Небосвод», бот для индивидуального разбора натальной карты.", 0.6)
    await send_bot(context, chat_id,
                    "Начнём с даты. Пришлите её в формате ДД.ММ.ГГГГ, например 20.08.1993.", 0.8)
    return ASK_DATE


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


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("Задайте переменную окружения BOT_TOKEN с токеном от @BotFather.")

    start_health_server()

    application = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start), CallbackQueryHandler(restart_button, pattern=f"^{RESTART_CB}$")],
        states={
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

    log.info("Небосвод запущен, жду сообщений…")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
