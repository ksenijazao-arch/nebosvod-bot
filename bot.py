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
import base64
import json
import logging
import os
import random
import re
import threading
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg2

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, TypeHandler, filters,
)

import astro_calc as ac
import content as ct
import content_extended as ct2
import gauge

DATABASE_URL = os.environ.get("DATABASE_URL")
BROADCAST_PASSWORD = os.environ.get("BROADCAST_PASSWORD", "")
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY", "")

FEATURE_PRICE = {"tomorrow": 100, "compat": 199, "numerology": 99, "money_ritual": 99, "extended_natal": 249}
FEATURE_LABEL = {"tomorrow": "«Что ждёт меня завтра» на сутки", "compat": "разбор совместимости", "numerology": "число жизненного пути", "money_ritual": "денежный ритуал по карте", "extended_natal": "расширенный разбор натальной карты"}


def db_connect():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def db_init():
    if not DATABASE_URL:
        return
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS users (chat_id BIGINT PRIMARY KEY, first_seen TIMESTAMPTZ DEFAULT now())")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS chart_json TEXT")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS date_str TEXT")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS gender TEXT")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS source TEXT")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                feature TEXT NOT NULL,
                yookassa_payment_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                amount NUMERIC NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now(),
                valid_until TIMESTAMPTZ
            )
        """)
        conn.commit()


def db_remember_user(chat_id, source=None):
    if not DATABASE_URL:
        return
    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (chat_id, source) VALUES (%s, %s) "
                "ON CONFLICT (chat_id) DO UPDATE SET source = COALESCE(users.source, EXCLUDED.source)",
                (chat_id, source),
            )
            conn.commit()
    except Exception:
        logging.exception("не удалось сохранить пользователя")


def db_all_users():
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT chat_id FROM users")
        return [row[0] for row in cur.fetchall()]


def db_get_email(chat_id):
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT email FROM users WHERE chat_id=%s", (chat_id,))
        row = cur.fetchone()
        return row[0] if row else None


def db_set_email(chat_id, email):
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET email=%s WHERE chat_id=%s", (email, chat_id))
        conn.commit()


def db_save_chart(chat_id, chart, date_str, gender):
    """Сохраняет готовый разбор в базу, чтобы он пережил перезапуск бота.
    В память (user_data) это не пишет, это отдельная задача вызывающего
    кода, здесь только постоянное хранилище."""
    if not DATABASE_URL:
        return
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET chart_json=%s, date_str=%s, gender=%s WHERE chat_id=%s",
            (json.dumps(chart), date_str, gender, chat_id),
        )
        conn.commit()


def db_get_saved_chart(chat_id):
    """Возвращает {'chart':.., 'date_str':.., 'gender':..} из базы, если
    там есть сохранённый разбор для этого chat_id, иначе None."""
    if not DATABASE_URL:
        return None
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT chart_json, date_str, gender FROM users WHERE chat_id=%s", (chat_id,))
        row = cur.fetchone()
        if not row or not row[0]:
            return None
        return {"chart": json.loads(row[0]), "date_str": row[1], "gender": row[2]}


def db_clear_chart(chat_id):
    """Стирает сохранённый разбор в базе. Вызывается только когда человек
    сам нажимает «Начать заново», не при обычной работе бота."""
    if not DATABASE_URL:
        return
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET chart_json=NULL, date_str=NULL, gender=NULL WHERE chat_id=%s", (chat_id,))
        conn.commit()


def db_has_access(chat_id, feature):
    if not DATABASE_URL:
        return False
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM payments WHERE chat_id=%s AND feature=%s AND status='succeeded' "
            "AND (valid_until IS NULL OR valid_until > now()) LIMIT 1",
            (chat_id, feature),
        )
        return cur.fetchone() is not None


def db_create_pending_payment(chat_id, feature, yookassa_payment_id, amount):
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (chat_id, feature, yookassa_payment_id, status, amount) "
            "VALUES (%s, %s, %s, 'pending', %s) RETURNING id",
            (chat_id, feature, yookassa_payment_id, amount),
        )
        row_id = cur.fetchone()[0]
        conn.commit()
        return row_id


def db_latest_pending_payment(chat_id, feature):
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, yookassa_payment_id FROM payments WHERE chat_id=%s AND feature=%s "
            "AND status='pending' ORDER BY created_at DESC LIMIT 1",
            (chat_id, feature),
        )
        return cur.fetchone()


def db_mark_succeeded(row_id, feature):
    valid_until = None if feature != "tomorrow" else "now() + interval '24 hours'"
    with db_connect() as conn, conn.cursor() as cur:
        if valid_until:
            cur.execute(f"UPDATE payments SET status='succeeded', valid_until={valid_until} WHERE id=%s", (row_id,))
        else:
            cur.execute("UPDATE payments SET status='succeeded' WHERE id=%s", (row_id,))
        conn.commit()


def _yookassa_request(method, path, body=None):
    """Синхронный запрос к API ЮKassa, вызывается через executor, чтобы не
    блокировать основной цикл бота. Базовая авторизация shopId/secretKey,
    как того требует документация ЮKassa."""
    auth = base64.b64encode(f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    data = None
    if body is not None:
        headers["Idempotence-Key"] = str(uuid.uuid4())
        data = json.dumps(body).encode()
    req = urllib.request.Request(f"https://api.yookassa.ru/v3/{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        logging.error("ЮKassa HTTP ошибка %s: %s", e.code, e.read().decode(errors="ignore"))
        return None
    except Exception:
        logging.exception("ЮKassa: не удалось выполнить запрос")
        return None


async def yookassa_create_payment(amount, description, return_url, email):
    """Создаёт платёж в ЮKassa, возвращает (payment_id, confirmation_url)
    или (None, None) при ошибке. Чек обязателен по 54-ФЗ, без него ЮKassa
    отвечает 400 invalid_request; email нужен, чтобы было куда его прислать."""
    body = {
        "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": description,
        "receipt": {
            "customer": {"email": email},
            "items": [{
                "description": description,
                "quantity": "1.00",
                "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
                "vat_code": 1,
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }],
        },
    }
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _yookassa_request, "POST", "payments", body)
    if not result or "id" not in result:
        return None, None
    return result["id"], result.get("confirmation", {}).get("confirmation_url")


async def yookassa_check_payment(payment_id):
    """Возвращает текущий статус платежа ('pending', 'succeeded', 'canceled'
    и т.д.) или None при ошибке связи."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _yookassa_request, "GET", f"payments/{payment_id}")
    return result.get("status") if result else None


async def payment_gate(chat_id: int, context: ContextTypes.DEFAULT_TYPE, feature: str) -> bool:
    """Проверяет доступ к платной функции. Если уже оплачено и действует,
    возвращает True, вызывающий код продолжает как обычно. Иначе сначала
    показывает, что человек получит (один раз за попытку, не повторяет
    при возврате после почты), затем спрашивает почту, если её ещё нет, и
    только потом создаёт платёж в ЮKassa. В обоих случаях, кроме успешного
    доступа, возвращает False, вызывающий код должен остановиться."""
    if db_has_access(chat_id, feature):
        return True

    pitch_flag = f"pitch_shown_{feature}"
    if not context.user_data.get(pitch_flag):
        context.user_data[pitch_flag] = True
        await context.bot.send_message(chat_id=chat_id, text=ct.FEATURE_PITCH[feature])

    email = context.user_data.get("email") or db_get_email(chat_id)
    if not email:
        context.user_data["awaiting_email_for"] = feature
        await context.bot.send_message(
            chat_id=chat_id,
            text="Одна короткая деталь перед оплатой: напишите свою почту, чтобы вы получили подтверждение и чек.",
            reply_markup=ForceReply(input_field_placeholder="ваша@почта.ру"),
        )
        return False
    context.user_data["email"] = email

    amount = FEATURE_PRICE[feature]
    payment_id, url = await yookassa_create_payment(
        amount, f"Небосвод: {FEATURE_LABEL[feature]}", "https://t.me/nebosvod_astro_bot", email
    )
    if not payment_id or not url:
        await context.bot.send_message(
            chat_id=chat_id, text="Не получилось создать платёж, попробуйте, пожалуйста, ещё раз через минуту."
        )
        return False
    db_create_pending_payment(chat_id, feature, payment_id, amount)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Оплатить", url=url)],
        [InlineKeyboardButton("✅ Я оплатил(а)", callback_data=f"paycheck:{feature}")],
    ])
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Эта часть платная, {amount} ₽. Оплатите по кнопке ниже, а после нажмите «Я оплатил(а)», я проверю и сразу продолжу.",
        reply_markup=keyboard,
    )
    return False


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def restore_chart_if_needed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Самый ранний обработчик из всех, стоит перед абсолютно всем
    остальным и никогда не останавливает обработку. Если бот перезапустился
    (память user_data чистая), но в базе для этого chat_id сохранён прошлый
    разбор, тихо подставляет его обратно в user_data, чтобы человеку не
    приходилось проходить дату, время и город заново только из-за того,
    что сервер перезапустился, не потому что он сам этого просил."""
    if context.user_data.get("chart"):
        return
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return
    saved = db_get_saved_chart(chat_id)
    if saved:
        context.user_data["chart"] = saved["chart"]
        context.user_data["date_str"] = saved["date_str"]
        context.user_data["gender"] = saved["gender"]


async def text_intercept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Единый ранний перехватчик для любого текста, который не должен идти
    в обычные диалоги: кнопки постоянного меню, ожидание вопроса в
    поддержку, ожидание почты перед оплатой. Собран в одну функцию
    намеренно: несколько отдельных обработчиков с широким фильтром в
    одной и той же группе конфликтуют между собой, движок отдаёт
    сообщение только первому подходящему и до остальных не доходит."""
    text = (update.message.text or "").strip()
    chat_id = update.effective_chat.id

    if text == "📋 Меню":
        await send_full_menu(update, context)
        raise ApplicationHandlerStop
    if text == "🆘 Поддержка":
        await support_start(update, context)
        raise ApplicationHandlerStop

    if context.user_data.get("awaiting_support_message"):
        del context.user_data["awaiting_support_message"]
        if ADMIN_CHAT_ID:
            try:
                await context.bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=f"🆘 Вопрос от пользователя {chat_id}:\n\n{text}")
            except Exception:
                logging.exception("Не удалось переслать вопрос в поддержку")
        await update.message.reply_text("Спасибо, вопрос передала, отвечу как можно быстрее.", reply_markup=PERSISTENT_KEYBOARD)
        raise ApplicationHandlerStop

    feature = context.user_data.get("awaiting_email_for")
    if feature:
        if not EMAIL_RE.match(text):
            await update.message.reply_text(
                "Это не похоже на почту, пришлите, пожалуйста, в формате имя@почта.ру.",
                reply_markup=ForceReply(input_field_placeholder="ваша@почта.ру"),
            )
            raise ApplicationHandlerStop
        db_set_email(chat_id, text)
        context.user_data["email"] = text
        del context.user_data["awaiting_email_for"]
        await update.message.reply_text("Спасибо, записала. Продолжаю с оплатой.", reply_markup=PERSISTENT_KEYBOARD)
        if feature == "compat":
            await _start_compat_core(chat_id, context)
        elif feature == "numerology":
            await _numerology_core(chat_id, context)
        elif feature == "tomorrow":
            await _tomorrow_menu_core(chat_id, context)
        elif feature == "money_ritual":
            await _money_ritual_core(chat_id, context)
        raise ApplicationHandlerStop


async def payment_confirm_compat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отдельная точка входа именно для совместимости: это часть compat_conv,
    поэтому обязана вернуть состояние диалога, как и start_compat, иначе бот
    забудет, что дальше ждёт дату партнёра."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    row = db_latest_pending_payment(chat_id, "compat")
    if not row:
        await context.bot.send_message(chat_id=chat_id, text="Не нашла платёж для проверки. Попробуйте начать заново.")
        return ConversationHandler.END
    row_id, yookassa_payment_id = row
    status = await yookassa_check_payment(yookassa_payment_id)
    if status == "succeeded":
        db_mark_succeeded(row_id, "compat")
        await context.bot.send_message(chat_id=chat_id, text="Оплата прошла! Продолжаю.")
        return await _start_compat_core(chat_id, context)
    elif status in ("pending", "waiting_for_capture"):
        await context.bot.send_message(
            chat_id=chat_id,
            text="Пока не вижу подтверждения оплаты. Если только что оплатили, подождите полминуты и нажмите кнопку ещё раз.",
        )
        return ConversationHandler.END
    else:
        await context.bot.send_message(
            chat_id=chat_id, text="Оплата не прошла или была отменена. Попробуйте начать заново с той же кнопки."
        )
        return ConversationHandler.END


async def payment_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Для числа жизненного пути и «завтра»: обе не многошаговые, обычный
    обработчик без состояний подходит без проблем."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    feature = query.data.split(":", 1)[1]

    row = db_latest_pending_payment(chat_id, feature)
    if not row:
        await context.bot.send_message(chat_id=chat_id, text="Не нашла платёж для проверки. Попробуйте начать заново.")
        return
    row_id, yookassa_payment_id = row
    status = await yookassa_check_payment(yookassa_payment_id)

    if status == "succeeded":
        db_mark_succeeded(row_id, feature)
        await context.bot.send_message(chat_id=chat_id, text="Оплата прошла! Продолжаю.")
        if feature == "numerology":
            await _numerology_core(chat_id, context)
        elif feature == "tomorrow":
            await _tomorrow_menu_core(chat_id, context)
        elif feature == "money_ritual":
            await _money_ritual_core(chat_id, context)
        elif feature == "extended_natal":
            await _extended_natal_core(chat_id, context)
    elif status in ("pending", "waiting_for_capture"):
        await context.bot.send_message(
            chat_id=chat_id,
            text="Пока не вижу подтверждения оплаты. Если только что оплатили, подождите полминуты и нажмите кнопку ещё раз.",
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id, text="Оплата не прошла или была отменена. Попробуйте начать заново с той же кнопки."
        )


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("nebosvod")

ASK_GENDER, ASK_DATE, ASK_TIME, ASK_CITY, ASK_PARTNER_DATE, ASK_PARTNER_TIME, ASK_PARTNER_CITY = range(7)

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]

SKIP_TIME_CB = "skip_time"
SKIP_CITY_CB = "skip_city"
GENDER_CB_PREFIX = "gender:"
COMPAT_CB = "compat"
NUMEROLOGY_CB = "numerology"
TOMORROW_CB = "tomorrow"
TOMORROW_WESTERN_CB = "tmrw_west"
TOMORROW_CHOGHADIYA_CB = "tmrw_chog"
MONEY_RITUAL_CB = "money_ritual"
SKIP_PARTNER_TIME_CB = "skip_ptime"
SKIP_PARTNER_CITY_CB = "skip_pcity"
FORECAST_CB = "forecast"
RESTART_CB = "restart"
SPHERE_CB_PREFIX = "sphere:"
UNLIVED_CB = "unlived"
HARMONY_CB = "harmony"
EXTENDED_NATAL_CB = "extended_natal"


def fmt_date(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year}"


async def typing_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, seconds: float):
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    await asyncio.sleep(seconds)


async def send_bot(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, delay: float = 1.0, **kwargs):
    await typing_delay(context, chat_id, delay)
    await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)


# ---------- разговор: сбор даты, времени, города ----------

def gender_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Женщина", callback_data=f"{GENDER_CB_PREFIX}female"),
        InlineKeyboardButton("Мужчина", callback_data=f"{GENDER_CB_PREFIX}male"),
    ]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    source = context.args[0][:60] if context.args else None
    db_remember_user(chat_id, source)

    if context.user_data.get("chart"):
        await send_bot(context, chat_id, "С возвращением! Ваш разбор уже готов, не нужно проходить его заново.", 0.5)
        await send_menu(context, chat_id)
        return ConversationHandler.END

    context.user_data.clear()
    avatar_path = os.path.join(os.path.dirname(__file__), "avatar.png")
    if os.path.exists(avatar_path):
        with open(avatar_path, "rb") as f:
            await context.bot.send_photo(chat_id=chat_id, photo=f)
    await send_bot(context, chat_id,
                    "Здравствуйте! Я «Небосвод», бот для индивидуального разбора натальной карты.", 0.6)
    await send_bot(
        context, chat_id,
        "Для начала один короткий вопрос, он важен для того, как будет звучать весь ваш разбор дальше.",
        0.6, reply_markup=gender_keyboard(),
    )
    return ASK_GENDER


async def got_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    value = query.data[len(GENDER_CB_PREFIX):]
    if value not in ("male", "female"):
        return ASK_GENDER
    context.user_data["gender"] = value
    await query.edit_message_reply_markup(reply_markup=None)
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


# ---------- совместимость (синастрия) ----------

async def start_compat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    return await _start_compat_core(query.message.chat_id, context)


async def _start_compat_core(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.user_data.get("chart"):
        await context.bot.send_message(chat_id=chat_id, text="Сначала пройдите разбор заново: /start")
        return ConversationHandler.END
    if not await payment_gate(chat_id, context, "compat"):
        return ConversationHandler.END
    avatar_path = os.path.join(os.path.dirname(__file__), "avatar.png")
    if os.path.exists(avatar_path):
        with open(avatar_path, "rb") as f:
            await context.bot.send_photo(chat_id=chat_id, photo=f, caption="💞 Совместимость двух карт")
    await send_bot(
        context, chat_id,
        "Хорошо, сравним карты. Дата рождения партнёра, в формате ДД.ММ.ГГГГ, например 20.08.1993.",
        0.6,
    )
    return ASK_PARTNER_DATE


async def got_partner_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", text)
    if not m:
        await update.message.reply_text("Не разобрала дату. Пришлите в формате ДД.ММ.ГГГГ, например 20.08.1993.")
        return ASK_PARTNER_DATE
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        date(year, month, day)
    except ValueError:
        await update.message.reply_text("Такой даты не существует. Проверьте число и месяц, и пришлите ещё раз.")
        return ASK_PARTNER_DATE

    context.user_data["partner_date_str"] = f"{year:04d}-{month:02d}-{day:02d}"
    chat_id = update.effective_chat.id
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Не знаю точно", callback_data=SKIP_PARTNER_TIME_CB)]])
    await send_bot(
        context, chat_id,
        "Время рождения партнёра, если известно, в формате ЧЧ:ММ. Если нет, нажмите кнопку ниже.",
        0.6, reply_markup=keyboard,
    )
    return ASK_PARTNER_TIME


async def got_partner_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    m = re.match(r"^(\d{1,2})[:.,](\d{2})$", text)
    if not m or not (0 <= int(m.group(1)) <= 23) or not (0 <= int(m.group(2)) <= 59):
        await update.message.reply_text("Не разобрала время. Формат ЧЧ:ММ, например 14:15, или нажмите «Не знаю точно» выше.")
        return ASK_PARTNER_TIME
    context.user_data["partner_time_str"] = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    return await ask_partner_city(update, context)


async def skip_partner_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["partner_time_str"] = ""
    await query.edit_message_reply_markup(reply_markup=None)
    return await ask_partner_city(update, context, chat_id=query.message.chat_id)


async def ask_partner_city(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int = None) -> int:
    chat_id = chat_id or update.effective_chat.id
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить", callback_data=SKIP_PARTNER_CITY_CB)]])
    await send_bot(
        context, chat_id,
        "И город рождения партнёра, если известен.",
        0.6, reply_markup=keyboard,
    )
    return ASK_PARTNER_CITY


async def got_partner_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["partner_city"] = update.message.text.strip()
    return await deliver_synastry(update, context, chat_id=update.effective_chat.id)


async def skip_partner_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["partner_city"] = ""
    await query.edit_message_reply_markup(reply_markup=None)
    return await deliver_synastry(update, context, chat_id=query.message.chat_id)


async def deliver_synastry(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> int:
    await send_bot(context, chat_id, "Смотрю, как соотносятся ваши карты…", 1.2)

    chart = context.user_data["chart"]
    partner_chart = ac.compute_chart(
        context.user_data["partner_date_str"],
        context.user_data.get("partner_time_str", ""),
        context.user_data.get("partner_city", ""),
    )

    moon_a_sid = ac.sidereal_lon(ac.point_lon(chart["moon"]), chart["birth_jd"])
    moon_b_sid = ac.sidereal_lon(ac.point_lon(partner_chart["moon"]), partner_chart["birth_jd"])
    nak_a = ac.nakshatra_of(moon_a_sid)["name"]
    nak_b = ac.nakshatra_of(moon_b_sid)["name"]
    gana_a, gana_b = ac.GANA_OF_NAKSHATRA[nak_a], ac.GANA_OF_NAKSHATRA[nak_b]
    nadi_a, nadi_b = ac.NADI_OF_NAKSHATRA[nak_a], ac.NADI_OF_NAKSHATRA[nak_b]
    gana_key = "_".join(sorted([gana_a, gana_b])) if gana_a != gana_b else f"{gana_a}_{gana_a}"
    gana_key = gana_key if gana_key in ct.GANA_TEXTS else "_".join(sorted([gana_a, gana_b], reverse=True))
    nadi_key = "same" if nadi_a == nadi_b else "different"

    await send_bot(
        context, chat_id,
        f"🕉️ Начнём с ведического: ваша накшатра {nak_a}, у партнёра {nak_b}.\n\n"
        f"{ct.GANA_TEXTS[gana_key]}\n\n{ct.NADI_TEXTS[nadi_key]}",
        2.0,
    )

    lines = []
    summary_entries = []  # (label, aspect, content_key) в порядке приоритета, для итоговой сводки

    def headline(aspect):
        percent, tag = ct.ASPECT_HEADLINE[aspect]
        return f"{tag}: {percent}" if percent else tag

    for key_a, key_b, label in ct.SYNASTRY_PAIRS:
        aspect = ac.natal_aspect(ac.point_lon(chart[key_a]), ac.point_lon(partner_chart[key_b]))
        text = ct.SYNASTRY_TEXTS[f"{key_a}_{key_b}"][aspect]
        lines.append(f"{label}. {headline(aspect)}\n{text}")
        summary_entries.append((label, aspect, f"{key_a}_{key_b}"))

    for key_a, key_b, label, content_key in ct.SYNASTRY_CROSS_PAIRS:
        aspect1 = ac.natal_aspect(ac.point_lon(chart[key_a]), ac.point_lon(partner_chart[key_b]))
        aspect2 = ac.natal_aspect(ac.point_lon(chart[key_b]), ac.point_lon(partner_chart[key_a]))
        text1 = ct.SYNASTRY_TEXTS[content_key][aspect1]
        if aspect2 == aspect1:
            lines.append(f"{label}. {headline(aspect1)}\n{text1}")
        else:
            text2 = ct.SYNASTRY_TEXTS[content_key][aspect2]
            lines.append(
                f"{label}. {headline(aspect1)}\n{text1}\n\n"
                f"И в обратную сторону, {headline(aspect2)}: {text2}"
            )
        summary_entries.append((label, aspect1, content_key))

    if chart["has_time"] and chart["rising"] and partner_chart["has_time"] and partner_chart["rising"]:
        asc_aspect = ac.natal_aspect(ac.point_lon(chart["rising"]), ac.point_lon(partner_chart["rising"]))
        lines.append(f"{ct.SYNASTRY_ASC_LABEL}. {headline(asc_aspect)}\n{ct.SYNASTRY_TEXTS['asc_asc'][asc_aspect]}")
        summary_entries.append((ct.SYNASTRY_ASC_LABEL, asc_aspect, "asc_asc"))

    for chunk_start in range(0, len(lines), 2):
        chunk = lines[chunk_start:chunk_start + 2]
        await send_bot(context, chat_id, "\n\n".join(chunk), 1.8)

    strengths = [(label, key) for label, aspect, key in summary_entries if aspect in ("conjunction", "trine")]
    growth = [(label, key) for label, aspect, key in summary_entries if aspect in ("square", "opposition")]
    harmonious_count = len(strengths)
    total_count = len(summary_entries)

    summary_parts = [f"📊 По цифрам: {harmonious_count} {ac.axis_word(harmonious_count)} из {total_count} гармоничные."]
    if strengths:
        summary_parts.append("🌟 Сильные стороны этой пары: " + ", ".join(l for l, k in strengths[:3]) + ".")
    if growth:
        summary_parts.append(f"🎯 Главная точка роста: {growth[0][0]}.")
    await send_bot(context, chat_id, "\n\n".join(summary_parts), 1.6)

    advice_key = growth[0][1] if growth else "fallback"
    await send_bot(context, chat_id, f"📝 Совет на эту неделю: {ct.SYNASTRY_WEEKLY_ADVICE[advice_key]}", 1.3)

    await send_menu(context, chat_id)
    return ConversationHandler.END


async def numerology(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _numerology_core(query.message.chat_id, context)


async def _numerology_core(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    date_str = context.user_data.get("date_str")
    if not date_str:
        await context.bot.send_message(chat_id=chat_id, text="Сначала пройдите разбор заново: /start")
        return
    if not await payment_gate(chat_id, context, "numerology"):
        return
    year, month, day = (int(x) for x in date_str.split("-"))
    steps = ac.life_path_steps(day, month, year)
    number = steps[-1]

    digits = f"{day:02d}{month:02d}{year:04d}"
    digit_sum = " + ".join(digits)
    chain = " → ".join(str(s) for s in steps)
    date_label = f"{day:02d}.{month:02d}.{year:04d}"

    await send_bot(context, chat_id, "Считаю число жизненного пути по дате рождения…", 0.9)
    await send_bot(
        context, chat_id,
        "В нумерологии число жизненного пути получают одним и тем же способом уже больше века: "
        "складывают все цифры полной даты рождения и сворачивают сумму до одной цифры, "
        "кроме чисел 11, 22 и 33, их принято оставлять как есть, если они выпали по пути.\n\n"
        f"Ваша дата {date_label}: {digit_sum} = {chain}.",
        1.8,
    )
    await send_bot(context, chat_id, ct.LIFE_PATH_TEXTS[str(number)], 1.5)

    this_year = date.today().year
    py_number = ac.personal_year_number(day, month, this_year)
    await send_bot(
        context, chat_id,
        f"И ещё одно число, которое обновляется каждый год само: число {this_year} года для вас {py_number}.\n\n"
        f"{ct.PERSONAL_YEAR_TEXTS[str(py_number)]}",
        1.6,
    )

    karmic = ac.karmic_debt_number(day)
    if karmic:
        await send_bot(context, chat_id, ct.KARMIC_DEBT_TEXTS[str(karmic)], 1.4)

    chart = context.user_data.get("chart")
    if chart and chart["has_time"] and chart["rising"]:
        natal_sun_lon = ac.point_lon(chart["sun"])
        sr_jd = ac.find_solar_return_jd(natal_sun_lon, this_year, month, day)
        today_jd = ac.to_jd(date.today().year, date.today().month, date.today().day, 0, 0)
        if sr_jd < today_jd:
            sr_jd = ac.find_solar_return_jd(natal_sun_lon, this_year + 1, month, day)
        sr_rising = ac.sign_of(ac.ascendant(sr_jd, chart["city"]["lat"], chart["city"]["lon"]))
        sr_year, sr_month, sr_day = ac.jd_to_ymd(sr_jd)
        await send_bot(
            context, chat_id,
            f"И западный аналог того же вопроса, солнечное возвращение: точный момент, когда Солнце в этом году "
            f"встаёт ровно на ваше натальное место, {sr_day} {['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'][sr_month-1]} {sr_year}.\n\n"
            f"{ct.SOLAR_RETURN_TEXTS[sr_rising['sign']]}",
            1.8,
        )

    today = date.today()
    age = today.year - year - (1 if (today.month, today.day) < (month, day) else 0)
    pins = ac.pinnacles(day, month, year)
    lines = ["И ещё один слой, числа вершин, четыре периода жизни, у каждого своё число и свои годы:\n"]
    for i, p in enumerate(pins, 1):
        end_label = f"до {p['end_age']} лет" if p["end_age"] is not None else "до конца жизни"
        is_current = p["start_age"] <= age and (p["end_age"] is None or age < p["end_age"])
        marker = " 👈 сейчас у вас этот период" if is_current else ""
        lines.append(f"{i}. Число {p['number']}, с {p['start_age']} {end_label}{marker}")
    await send_bot(context, chat_id, "\n".join(lines), 1.6)

    current_pin = next(p for p in pins if p["start_age"] <= age and (p["end_age"] is None or age < p["end_age"]))
    await send_bot(context, chat_id, ct.PINNACLE_TEXTS[str(current_pin["number"])], 1.4)

    card_path = os.path.join(os.path.dirname(__file__), f"{number}.png")
    if os.path.exists(card_path):
        with open(card_path, "rb") as f:
            await context.bot.send_photo(
                chat_id=chat_id, photo=f,
                caption="Сохраните эту карточку и поставьте на заставку телефона. Видеть своё число каждый день, простая практика: она помогает держать в уме главное направление, особенно когда строите планы вперёд.",
            )

    await send_menu(context, chat_id)


async def tomorrow_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _tomorrow_menu_core(query.message.chat_id, context)


async def _tomorrow_menu_core(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    chart = context.user_data.get("chart")
    if not chart or not (chart["has_time"] and chart["city"].get("lat")):
        await context.bot.send_message(
            chat_id=chat_id,
            text="Для этого нужен город рождения с известными координатами. Пройдите разбор заново и укажите город.",
        )
        return
    if not await payment_gate(chat_id, context, "tomorrow"):
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏰ Западные часы", callback_data=TOMORROW_WESTERN_CB)],
        [InlineKeyboardButton("🕉️ Чогхадия", callback_data=TOMORROW_CHOGHADIYA_CB)],
    ])
    await send_bot(
        context, chat_id,
        "Есть две традиции для этого, обе настоящие, просто разные: западные планетные часы или ведическая чогхадия. Что показать?",
        0.8, reply_markup=keyboard,
    )


async def money_ritual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _money_ritual_core(query.message.chat_id, context)


async def _money_ritual_core(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    chart = context.user_data.get("chart")
    if not chart or not chart["has_time"]:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Для этого нужно точное время рождения, чтобы верно вычислить асцендент. Пройдите разбор заново и укажите время.",
        )
        return
    if not await payment_gate(chat_id, context, "money_ritual"):
        return
    planet = ac.money_planet(chart["rising"]["sign"])
    second_house_sign = ac.sign_in_house(chart["rising"]["sign"], 2)
    full_text = ct.MONEY_RITUAL_TEXTS[planet]
    planet_label = ct.PLANET_LABEL[planet]

    reasoning = (
        f"В вашей карте на втором доме, доме денег, стоит {second_house_sign}, управитель этого знака, "
        f"{planet_label}. Вот почему {planet_label} и есть ваша денежная планета.\n\n"
    )
    header_marker = f"{planet_label}, ваш день"
    idx = full_text.index(header_marker)
    line_end = full_text.index("\n\n", idx) + 2
    full_text = full_text[:line_end] + reasoning + full_text[line_end:]

    part1, rest = full_text.split("\n\nСам ритуал, по шагам:\n", 1)
    steps, rest = rest.split("\n\nКак часто:", 1)
    freq_and_rest = "Как часто:" + rest
    freq, avoid = freq_and_rest.split("\n\nЧего избегать:", 1)

    await send_bot(context, chat_id, part1, 1.8)
    await send_bot(context, chat_id, f"🪙 {planet_label}\n\nСам ритуал, по шагам:\n" + steps, 1.6)
    await send_bot(context, chat_id, freq, 1.4)
    await send_bot(context, chat_id, "Чего избегать:" + avoid, 1.0)

    image_path = os.path.join(os.path.dirname(__file__), f"{planet}.png")
    if os.path.exists(image_path):
        with open(image_path, "rb") as f:
            await context.bot.send_photo(
                chat_id=chat_id, photo=f,
                caption="Это ваша числовая янтра, настоящая традиционная геометрия, не просто картинка. По традиции на неё смотрят несколько секунд перед тем, как произнести денежное намерение, задерживая взгляд на центре сетки, это способ сосредоточиться именно на этой планете. Сохраните изображение, чтобы использовать его каждую неделю в свой день.",
            )
    await send_menu(context, chat_id)


def _tomorrow_setup(chart):
    tomorrow = date.today() + timedelta(days=1)
    tz_offset = ac.utc_offset_hours(chart["city"]["tz"], tomorrow.year, tomorrow.month, tomorrow.day, 12, 0)
    jd_midnight = ac.to_jd(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0) - tz_offset / 24
    return tomorrow, jd_midnight, tz_offset


def _fmt_hm(h):
    h = h % 24
    hh, mm = int(h), int(round((h % 1) * 60))
    if mm == 60:
        mm = 0
        hh = (hh + 1) % 24
    return f"{hh:02d}:{mm:02d}"


async def tomorrow_western(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    chart = context.user_data.get("chart")
    if not chart:
        return
    tomorrow, jd_midnight, tz_offset = _tomorrow_setup(chart)
    hours = ac.planetary_hours(jd_midnight, chart["city"]["lat"], chart["city"]["lon"], tz_offset, tomorrow.weekday())
    day_hours = hours[:12]

    best_priority = ["jupiter", "venus", "mercury", "moon"]
    worst_priority = ["saturn", "mars", "sun"]
    best = next(h for p in best_priority for h in day_hours if h["planet"] == p)
    worst = next(h for p in worst_priority for h in day_hours if h["planet"] == p)

    date_label = tomorrow.strftime("%d.%m.%Y")
    await send_bot(
        context, chat_id,
        f"⏰ Завтра, {date_label}, по западным часам (время местное, по вашему городу рождения):\n\n"
        f"✅ Лучшее окно: {_fmt_hm(best['start'])}–{_fmt_hm(best['end'])}, {ct.PLANET_LABEL[best['planet']].lower()}. "
        f"{ct.PLANET_HOUR_TEXTS[best['planet']]}\n\n"
        f"⚠️ Стоит быть осторожнее: {_fmt_hm(worst['start'])}–{_fmt_hm(worst['end'])}, {ct.PLANET_LABEL[worst['planet']].lower()}. "
        f"{ct.PLANET_HOUR_TEXTS[worst['planet']]}",
        1.8,
    )

    lines = [
        "🔭 Полная сетка часов, если нужна точность. ✅ благоприятный час, 🟡 нейтральный, ⚠️ стоит быть аккуратнее:",
        "\nДнём:",
    ]
    for h in hours[:12]:
        lines.append(
            f"{_fmt_hm(h['start'])}–{_fmt_hm(h['end'])} {ct.PLANET_HOUR_MARK[h['planet']]} "
            f"{ct.PLANET_LABEL[h['planet']]} ({ct.PLANET_HOUR_GLOSS[h['planet']]})"
        )
    lines.append("\nНочью:")
    for h in hours[12:]:
        lines.append(
            f"{_fmt_hm(h['start'])}–{_fmt_hm(h['end'])} {ct.PLANET_HOUR_MARK[h['planet']]} "
            f"{ct.PLANET_LABEL[h['planet']]} ({ct.PLANET_HOUR_GLOSS[h['planet']]})"
        )
    await send_bot(context, chat_id, "\n".join(lines), 1.3)

    switch_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🕉️ А что скажет чогхадия?", callback_data=TOMORROW_CHOGHADIYA_CB)]])
    await context.bot.send_message(chat_id=chat_id, text="Хотите посмотреть и вторую систему на тот же день?", reply_markup=switch_kb)

    await send_menu(context, chat_id)


async def tomorrow_choghadiya(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    chart = context.user_data.get("chart")
    if not chart:
        return
    tomorrow, jd_midnight, tz_offset = _tomorrow_setup(chart)
    slots = ac.choghadiya(jd_midnight, chart["city"]["lat"], chart["city"]["lon"], tz_offset, tomorrow.weekday())
    day_slots = slots[:8]

    best_priority = ["amrit", "shubh", "labh", "chal"]
    worst_priority = ["kaal", "rog", "udveg"]
    best = next(s for p in best_priority for s in day_slots if s["name"] == p)
    worst = next(s for p in worst_priority for s in day_slots if s["name"] == p)

    date_label = tomorrow.strftime("%d.%m.%Y")
    await send_bot(
        context, chat_id,
        f"🕉️ Завтра, {date_label}, по чогхадии (время местное, по вашему городу рождения):\n\n"
        f"✅ Лучшее окно: {_fmt_hm(best['start'])}–{_fmt_hm(best['end'])}, {ct.CHOGHADIYA_LABEL[best['name']]}. "
        f"{ct.CHOGHADIYA_TEXTS[best['name']]}\n\n"
        f"⚠️ Стоит быть осторожнее: {_fmt_hm(worst['start'])}–{_fmt_hm(worst['end'])}, {ct.CHOGHADIYA_LABEL[worst['name']]}. "
        f"{ct.CHOGHADIYA_TEXTS[worst['name']]}",
        1.8,
    )

    lines = [
        "🔭 Полная сетка на день, если нужна точность. ✅ благоприятная чогхадия, 🟡 нейтральная, ❌ лучше переждать:",
        "\nДнём:",
    ]
    for s in slots[:8]:
        lines.append(f"{_fmt_hm(s['start'])}–{_fmt_hm(s['end'])} {ct.CHOGHADIYA_LABEL[s['name']]}")
    lines.append("\nНочью:")
    for s in slots[8:]:
        lines.append(f"{_fmt_hm(s['start'])}–{_fmt_hm(s['end'])} {ct.CHOGHADIYA_LABEL[s['name']]}")
    await send_bot(context, chat_id, "\n".join(lines), 1.3)

    switch_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏰ А что скажут западные часы?", callback_data=TOMORROW_WESTERN_CB)]])
    await context.bot.send_message(chat_id=chat_id, text="Хотите посмотреть и вторую систему на тот же день?", reply_markup=switch_kb)

    await send_menu(context, chat_id)


# ---------- сборка и отправка разбора ----------

SPHERE_EMOJI = {"money": "💰", "love": "❤️", "career": "💼", "health": "🌿"}


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{SPHERE_EMOJI[key]} {s['title']}", callback_data=f"{SPHERE_CB_PREFIX}{key}")]
        for key, s in ct.SPHERES.items()
    ] + [
        [InlineKeyboardButton("🔭 Узнать важные даты", callback_data=FORECAST_CB)],
        [InlineKeyboardButton("✨ Непрожитые жизни", callback_data=UNLIVED_CB)],
        [InlineKeyboardButton("🌌 Гармония моей карты, бесплатно", callback_data=HARMONY_CB)],
        [InlineKeyboardButton(f"🪐 Расширенный разбор карты, {FEATURE_PRICE['extended_natal']} ₽", callback_data=EXTENDED_NATAL_CB)],
        [InlineKeyboardButton("💞 Совместимость, 199 ₽", callback_data=COMPAT_CB)],
        [InlineKeyboardButton("🔢 Число жизненного пути, 99 ₽", callback_data=NUMEROLOGY_CB)],
        [InlineKeyboardButton("🌅 Что ждёт меня завтра, 100 ₽/сутки", callback_data=TOMORROW_CB)],
        [InlineKeyboardButton("🪙 Денежный ритуал по карте, 99 ₽", callback_data=MONEY_RITUAL_CB)],
        [InlineKeyboardButton("🔄 Начать заново", callback_data=RESTART_CB)],
    ])




PERSISTENT_KEYBOARD = ReplyKeyboardMarkup([["📋 Меню", "🆘 Поддержка"]], resize_keyboard=True)
MENU_BUTTON_TEXTS = ("📋 Меню", "🆘 Поддержка")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")


async def send_menu(context, chat_id):
    await context.bot.send_message(chat_id=chat_id, text="Готово.", reply_markup=PERSISTENT_KEYBOARD)


async def send_full_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    avatar_path = os.path.join(os.path.dirname(__file__), "avatar.png")
    if os.path.exists(avatar_path):
        with open(avatar_path, "rb") as f:
            await context.bot.send_photo(chat_id=chat_id, photo=f, caption="Что дальше?", reply_markup=main_menu_keyboard())
    else:
        await context.bot.send_message(chat_id=chat_id, text="Что дальше?", reply_markup=main_menu_keyboard())


async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["awaiting_support_message"] = True
    await update.message.reply_text(
        "Опишите, пожалуйста, что случилось или что хотели спросить, одним сообщением, отвечу как можно быстрее.",
        reply_markup=ForceReply(input_field_placeholder="ваш вопрос"),
    )


async def deliver_chart(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> int:
    await send_bot(context, chat_id, "Секунду, считаю положение светил на момент вашего рождения…", 1.1)

    chart = ac.compute_chart(
        context.user_data["date_str"],
        context.user_data.get("time_str", ""),
        context.user_data.get("city", ""),
    )
    context.user_data["chart"] = chart
    db_save_chart(chat_id, chart, context.user_data["date_str"], context.user_data.get("gender"))

    await send_bot(context, chat_id, "Готово. Начнём с большой тройки.", 0.6)

    await send_bot(
        context, chat_id,
        f"☀️ Ваше Солнце в знаке {ac.SIGN_GENITIVE[chart['sun']['sign']]}.\n\n{ct.SUN_TEXTS[chart['sun']['sign']]}",
        1.3,
    )
    await send_bot(
        context, chat_id,
        f"🌙 Луна {ac.v_predlog(chart['moon']['sign'])} {ac.SIGN_PREPOSITIONAL[chart['moon']['sign']]}. {ct.MOON_TEXTS[chart['moon']['sign']]}",
        1.0,
    )
    if chart["has_time"]:
        note = " (город не указан, расчёт приблизительный, по Москве)" if chart["used_default_city"] else ""
        await send_bot(
            context, chat_id,
            f"⬆️ Восходящий знак: {chart['rising']['sign']}{note}. {ct.RISING_TEXTS[chart['rising']['sign']]}",
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
        f"💬 Меркурий {ac.v_predlog(chart['mercury']['sign'])} {ac.SIGN_PREPOSITIONAL[chart['mercury']['sign']]}. {ct.MERCURY_TEXTS[chart['mercury']['sign']]}\n\n"
        f"🌸 Венера {ac.v_predlog(chart['venus']['sign'])} {ac.SIGN_PREPOSITIONAL[chart['venus']['sign']]}. {ct.VENUS_TEXTS[chart['venus']['sign']]}",
        1.4,
    )
    await send_bot(
        context, chat_id,
        f"🔥 Марс {ac.v_predlog(chart['mars']['sign'])} {ac.SIGN_PREPOSITIONAL[chart['mars']['sign']]}. {ct.MARS_TEXTS[chart['mars']['sign']]}\n\n"
        f"🍀 Юпитер {ac.v_predlog(chart['jupiter']['sign'])} {ac.SIGN_PREPOSITIONAL[chart['jupiter']['sign']]}. {ct.JUPITER_TEXTS[chart['jupiter']['sign']]}",
        1.4,
    )
    await send_bot(
        context, chat_id,
        f"⏳ Сатурн {ac.v_predlog(chart['saturn']['sign'])} {ac.SIGN_PREPOSITIONAL[chart['saturn']['sign']]}. {ct.SATURN_TEXTS[chart['saturn']['sign']]}",
        1.2,
    )

    balance = ac.chart_balance(chart)
    element_names = " и ".join(ct.ELEMENT_LABEL[e] for e in balance["dominant_elements"])
    element_texts = " ".join(ct.ELEMENT_PROFILES[e] for e in balance["dominant_elements"])
    modality_names = " и ".join(ct.MODALITY_LABEL[m] for m in balance["dominant_modalities"])
    modality_texts = " ".join(ct.MODALITY_PROFILES[m] for m in balance["dominant_modalities"])
    await send_bot(
        context, chat_id,
        f"И ещё один слой, общий баланс карты. По стихиям у вас сильнее всего {element_names}.\n\n{element_texts}",
        1.5,
    )
    await send_bot(
        context, chat_id,
        f"А по качествам сильнее всего {modality_names}.\n\n{modality_texts}",
        1.4,
    )

    ayanamsa = ac.lahiri_ayanamsa(chart["birth_jd"])
    moon_sid_lon = ac.sidereal_lon(ac.point_lon(chart["moon"]), chart["birth_jd"])
    moon_sid_sign = ac.sign_of(moon_sid_lon)
    sun_sid_sign = ac.sign_of(ac.sidereal_lon(ac.point_lon(chart["sun"]), chart["birth_jd"]))
    nakshatra = ac.nakshatra_of(moon_sid_lon)

    diff_note = ""
    if moon_sid_sign["sign"] != chart["moon"]["sign"]:
        diff_note = (
            f" Это отличается от вашей западной Луны в {ac.SIGN_PREPOSITIONAL[chart['moon']['sign']]}, "
            "и это другой, тоже настоящий зодиак, не ошибка."
        )
    await send_bot(
        context, chat_id,
        f"🕉️ И последний слой, ведический. Индийская астрология считает знаки от неподвижных звёзд, не от точки равноденствия, "
        f"и сейчас разница между двумя зодиаками около {ayanamsa:.0f}°. По этой системе ваше Солнце "
        f"{ac.v_predlog(sun_sid_sign['sign'])} {ac.SIGN_PREPOSITIONAL[sun_sid_sign['sign']]}, "
        f"а Луна {ac.v_predlog(moon_sid_sign['sign'])} {ac.SIGN_PREPOSITIONAL[moon_sid_sign['sign']]}.{diff_note}",
        1.6,
    )
    await send_bot(
        context, chat_id,
        f"Точнее Луну описывает накшатра, одна из 27 лунных стоянок: ваша {nakshatra['name']}, {nakshatra['pada']}-я четверть.\n\n"
        f"{ct.NAKSHATRA_TEXTS[nakshatra['name']]}",
        1.7,
    )

    now_jd = ac.to_jd(date.today().year, date.today().month, date.today().day, 0, 0)
    timeline = ac.dasha_timeline(chart["birth_jd"], moon_sid_lon, years_ahead=130)
    planet, start_jd, end_jd = ac.current_dasha(timeline, now_jd)
    current_idx = timeline.index((planet, start_jd, end_jd))
    next_planet, next_start_jd, _ = timeline[current_idx + 1]
    await send_bot(
        context, chat_id,
        f"И ещё один ведический слой: даша, система больших периодов жизни, у каждой планеты свой, от шести до двадцати лет, "
        f"и сейчас у вас идёт период {ct.DASHA_LABEL[planet]}, с {ac.jd_to_date_label(start_jd)} по {ac.jd_to_date_label(end_jd)}.\n\n"
        f"{ct.DASHA_TEXTS[planet]}\n\n"
        f"Следующий период {ct.DASHA_LABEL[next_planet]} начнётся {ac.jd_to_date_label(next_start_jd)}.",
        2.2,
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

    await send_bot(context, chat_id, f"Смотрю, что карта говорит про {ct.SPHERE_ACCUSATIVE[sphere_key]}…", 0.7)

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
                weight_bank = ct.PLANET_WEIGHT if h["variant"] == 0 else ct.PLANET_WEIGHT_V2
                if h["retrograde"]:
                    lead = f"{ct.PLANET_LABEL[h['planet_key']]} сейчас здесь, но движется попятно"
                else:
                    reason_bank = ct.PLANET_REASON if h["variant"] == 0 else ct.PLANET_REASON_V2
                    lead = reason_bank[h["planet_key"]]
                lines.append(f"\n📅 {h['date_label']}. {lead}: {action}. {weight_bank[h['planet_key']]}")
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

    moon_sid_lon = ac.sidereal_lon(ac.point_lon(chart["moon"]), chart["birth_jd"])
    now_jd_dasha = ac.to_jd(date.today().year, date.today().month, date.today().day, 0, 0)
    dasha_timeline = ac.dasha_timeline(chart["birth_jd"], moon_sid_lon, years_ahead=130)
    current_planet, _, _ = ac.current_dasha(dasha_timeline, now_jd_dasha)
    await send_bot(context, chat_id, ct.SPHERE_DASHA_TEXTS[sphere_key][current_planet], 1.0)

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
                2.4,
            )

        future_hits = ac.find_transit_hits(chart["north_node_lon"], "saturn", now, 6570)
        future_major = [h for h in future_hits if h["aspect_key"] in ("conjunction", "opposition")]
        if future_major:
            nxt = future_major[0]
            event_jd = now + nxt["day_offset"]
            ey, em, _ = ac.jd_to_ymd(event_jd)
            age = ey - by - (1 if (em, 1) < (bm, bd) else 0)
            month_year = f"{ct.MONTHS_PREP[em - 1]} {ey}"
            template = ct.NODE_SATURN_FUTURE[nxt["aspect_key"]]
            await send_bot(
                context, chat_id,
                template.format(age=age, year_word=ac.year_word(age), month_year=month_year, tie=ct.HOUSE_INFO[house_num]["tie"]),
                2.2,
            )

    for planet_key in ["venus", "mars", "mercury", "jupiter", "saturn"]:
        if ac.is_retrograde(planet_key, birth_jd):
            await send_bot(context, chat_id, "✨ " + ct.RETRO_NARRATIVES[planet_key], 2.2)
            break

    moon_sid_lon = ac.sidereal_lon(ac.point_lon(chart["moon"]), birth_jd)
    dasha_tl = ac.dasha_timeline(birth_jd, moon_sid_lon, years_ahead=130)
    current_planet, _, _ = ac.current_dasha(dasha_tl, now)
    supports = ct.DASHA_SUPPORTS_RETURN[current_planet]
    await send_bot(
        context, chat_id,
        ct.DASHA_RETURN_TEXT[supports].format(planet=ct.DASHA_LABEL[current_planet]),
        1.3,
    )

    await send_menu(context, chat_id)


# ---------- гармония карты (бесплатный спидометр) и расширенный разбор (платный) ----------

async def harmony(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    chart = context.user_data.get("chart")
    if not chart:
        await context.bot.send_message(chat_id=chat_id, text="Сначала пройдите разбор заново: /start")
        return

    await send_bot(context, chat_id, "Считаю аспекты между планетами и балл гармонии вашей карты…", 1.0)

    ext = ac.compute_extended_chart(chart)
    h = ac.harmony_score(ext)

    gauge_path = os.path.join("/tmp", f"harmony_{chat_id}.png")
    gauge.render_harmony_gauge(h["total"], h["planets_in_harmony"], gauge_path)

    with open(gauge_path, "rb") as f:
        await context.bot.send_photo(chat_id=chat_id, photo=f)
    try:
        os.remove(gauge_path)
    except OSError:
        pass

    await send_bot(context, chat_id, ct2.HARMONY_GAUGE_INTRO, 1.4)
    await send_bot(context, chat_id, ct2.harmony_level_text(h["total"]), 1.6)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🪐 Узнать, какие именно планеты — {FEATURE_PRICE['extended_natal']} ₽", callback_data=EXTENDED_NATAL_CB)],
    ])
    await context.bot.send_message(
        chat_id=chat_id,
        text="Спидометр показывает общий балл. В расширенном разборе видно, какая именно планета — ваш источник силы, а какая просит больше внимания.",
        reply_markup=keyboard,
    )
    await send_menu(context, chat_id)


async def extended_natal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _extended_natal_core(query.message.chat_id, context)


async def _extended_natal_core(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    chart = context.user_data.get("chart")
    if not chart:
        await context.bot.send_message(chat_id=chat_id, text="Сначала пройдите разбор заново: /start")
        return
    if not await payment_gate(chat_id, context, "extended_natal"):
        return

    if not (chart["has_time"] and chart["rising"]):
        await send_bot(
            context, chat_id,
            "Дома считаются только по точному времени рождения. Для этого разбора мне нужно и время, и город: "
            "пройдите разбор заново и укажите их, тогда пришлю полную версию, включая дома.",
            1.2,
        )

    ext = ac.compute_extended_chart(chart)
    has_houses = ext["houses"] is not None

    await send_bot(context, chat_id, "Собираю расширенный разбор — десять планет и три дополнительные точки…", 1.0)

    planet_order = ac.EXTENDED_PLANET_KEYS
    planet_label = {
        "sun": "☀️ Солнце", "moon": "🌙 Луна", "mercury": "☿️ Меркурий", "venus": "♀️ Венера",
        "mars": "♂️ Марс", "jupiter": "♃ Юпитер", "saturn": "♄ Сатурн",
        "uranus": "♅ Уран", "neptune": "♆ Нептун", "pluto": "♇ Плутон",
    }
    sign_texts_by_planet = {
        "sun": ct.SUN_TEXTS, "moon": ct.MOON_TEXTS, "mercury": ct.MERCURY_TEXTS, "venus": ct.VENUS_TEXTS,
        "mars": ct.MARS_TEXTS, "jupiter": ct.JUPITER_TEXTS, "saturn": ct.SATURN_TEXTS,
        "uranus": ct2.URANUS_TEXTS, "neptune": ct2.NEPTUNE_TEXTS, "pluto": ct2.PLUTO_TEXTS,
    }

    for key in planet_order:
        p = ext[key]
        lines = [f"{planet_label[key]} в {ac.SIGN_PREPOSITIONAL[p['sign']]}", "", sign_texts_by_planet[key][p["sign"]]]
        if has_houses:
            house_num = ext["houses"][key]
            lines.append("")
            lines.append(ct2.PLANET_HOUSE_TEXTS[key][house_num])
        await send_bot(context, chat_id, "\n".join(lines), 1.1)

    point_label = {"lilith": "⚸ Лилит", "vertex": "🔺 Вертекс", "fortune": "🍀 Парс Фортуны"}
    point_sign_texts = {"lilith": ct2.LILITH_TEXTS, "vertex": ct2.VERTEX_TEXTS, "fortune": ct2.FORTUNE_TEXTS}
    point_house_texts = {"lilith": ct2.LILITH_HOUSE_TEXTS, "vertex": ct2.VERTEX_HOUSE_TEXTS, "fortune": ct2.FORTUNE_HOUSE_TEXTS}

    for key in ac.POINT_KEYS:
        p = ext.get(key)
        if p is None:
            continue
        lines = [f"{point_label[key]} в {ac.SIGN_PREPOSITIONAL[p['sign']]}", "", point_sign_texts[key][p["sign"]]]
        if has_houses:
            house_num = ext["houses"][key]
            lines.append("")
            lines.append(point_house_texts[key][house_num])
        await send_bot(context, chat_id, "\n".join(lines), 1.1)

    h = ac.harmony_score(ext)
    strongest = max(h["per_planet"], key=h["per_planet"].get)
    weakest = min(h["per_planet"], key=h["per_planet"].get)
    await send_bot(
        context, chat_id,
        f"И главный вывод по гармонии карты: {planet_label[strongest]} у вас в самых слаженных аспектах с остальными "
        f"планетами — это ваш надёжный источник силы. А {planet_label[weakest]} чаще в напряжении с другими "
        f"планетами — с этой сферой стоит работать осознанно, не пускать на самотёк.",
        1.8,
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
        moon_sid_lon = ac.sidereal_lon(ac.point_lon(chart["moon"]), chart["birth_jd"])
        dasha_tl = ac.dasha_timeline(chart["birth_jd"], moon_sid_lon, years_ahead=130)
        for h in hits[:6]:
            text = ct.TRANSIT_TEXTS[h["planet_key"]][h["point_key"]][h["aspect_key"]]
            text = text[0].upper() + text[1:]
            house_note = ""
            if chart["has_time"] and chart["rising"]:
                house_num = ac.house_of_sign(h["transit_sign"], chart["rising"]["sign"])
                info = ct.HOUSE_INFO[house_num]
                house_note = (f" Сейчас {ct.PLANET_LABEL[h['planet_key']]} идёт через ваш {info['label']}, "
                              f"и заметнее всего это отразится на {info['tie']}.")
            event_jd = now + h["day_offset"]
            event_planet, _, _ = ac.current_dasha(dasha_tl, event_jd)
            dasha_note = f" К этой дате у вас будет идти период {ct.DASHA_LABEL[event_planet]}."
            await send_bot(
                context, chat_id,
                f"{h['date_label']}: {ct.PLANET_LABEL[h['planet_key']]} образует {ct.ASPECT_ACCUSATIVE[h['aspect_key']]} "
                f"с {ct.NATAL_INSTRUMENTAL[h['point_key']]}. {text}{house_note}{dasha_note}",
                0.95,
            )
        if len(hits) > 6:
            await send_bot(context, chat_id,
                            f"Это первые 6 дат из {len(hits)} найденных на два года вперёд, остальные дальше по времени.",
                            0.7)

    natal_points = {"sun": ac.point_lon(chart["sun"]), "moon": ac.point_lon(chart["moon"])}
    if chart["has_time"] and chart["rising"]:
        natal_points["rising"] = ac.point_lon(chart["rising"])
    eclipse_hits = ac.find_personal_eclipses(natal_points, now)
    if eclipse_hits:
        h = eclipse_hits[0]
        await send_bot(
            context, chat_id,
            f"🌑 И ещё, отдельно: {ac.jd_to_date_label(h['jd'])} затмение, одно из самых сильных, редких событий в астрологии вообще, "
            f"ложится почти точно на вашу личную точку карты, с орбисом {h['orb']}°.\n\n"
            f"{ct.ECLIPSE_TEXTS[h['type'] + '_' + h['point']]}",
            2.0,
        )

    await send_menu(context, chat_id)


async def restart_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    context.user_data.clear()
    db_clear_chart(chat_id)
    await send_bot(context, chat_id,
                    "Хорошо, начинаем заново.", 0.5,
                    reply_markup=gender_keyboard())
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


def _run_health_server(server):
    try:
        server.serve_forever()
    except Exception:
        logging.exception("health-сервер упал и больше не отвечает на проверки Render/UptimeRobot")


def start_health_server():
    """Render (и похожие площадки) ждут, что сервис слушает порт.
    Сам бот работает через постоянный опрос Telegram и порт не использует,
    так что здесь просто открываем его для проверки, что сервис жив.
    Ошибку в потоке теперь обязательно логируем, раньше поток мог тихо
    упасть, а основной бот продолжал бы работать как ни в чём не бывало,
    снаружи же Render показывал бы 502, будто всё мертво."""
    port = int(os.environ.get("PORT", "8080"))
    try:
        server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    except OSError:
        logging.exception("health-сервер не смог занять порт %s, вероятно порт уже занят", port)
        return
    threading.Thread(target=_run_health_server, args=(server,), daemon=True).start()
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


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or parts[1] != BROADCAST_PASSWORD or not BROADCAST_PASSWORD:
        await update.message.reply_text("Формат: /stats пароль")
        return
    if not DATABASE_URL:
        await update.message.reply_text("База данных не подключена.")
        return
    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE first_seen > now() - interval '7 days'")
            week = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE first_seen > now() - interval '1 day'")
            day = cur.fetchone()[0]
            cur.execute(
                "SELECT COALESCE(source, 'без метки'), COUNT(*) FROM users "
                "WHERE first_seen > now() - interval '14 days' "
                "GROUP BY source ORDER BY COUNT(*) DESC LIMIT 15"
            )
            by_source = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM users WHERE chart_json IS NOT NULL")
            finished = cur.fetchone()[0]
            cur.execute(
                "SELECT feature, COUNT(*), COALESCE(SUM(amount),0) FROM payments "
                "WHERE status='succeeded' GROUP BY feature ORDER BY COUNT(*) DESC"
            )
            by_feature = cur.fetchall()
            cur.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM payments WHERE status='succeeded'")
            paid_count, paid_sum = cur.fetchone()
    except Exception as e:
        await update.message.reply_text(f"Не удалось посчитать: {e}")
        return
    source_lines = "\n".join(f"  {name}: {count}" for name, count in by_source) or "  нет данных"
    feature_lines = "\n".join(
        f"  {FEATURE_LABEL.get(name, name)}: {count} шт. на {amount:.0f}₽" for name, count, amount in by_feature
    ) or "  пока нет оплат"
    await update.message.reply_text(
        f"Всего заходило в бота: {total}.\nЗа последние 7 дней: {week}.\nЗа последние сутки: {day}.\n"
        f"Дошли до бесплатного разбора: {finished}.\n\n"
        f"По источникам за 14 дней:\n{source_lines}\n\n"
        f"Оплаты всего: {paid_count} шт. на {paid_sum:.0f}₽\n{feature_lines}"
    )


async def reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reply chat_id текст, только для администратора (ADMIN_CHAT_ID),
    отправляет указанному пользователю сообщение от имени бота. Так можно
    ответить на вопрос, пришедший через кнопку «Поддержка»."""
    chat_id = update.effective_chat.id
    if not ADMIN_CHAT_ID or str(chat_id) != str(ADMIN_CHAT_ID):
        return
    text = update.message.text or ""
    parts = text.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].lstrip("-").isdigit():
        await update.message.reply_text("Формат: /reply id_пользователя текст ответа")
        return
    target_id, message = int(parts[1]), parts[2]
    try:
        await context.bot.send_message(chat_id=target_id, text=f"Ответ от поддержки:\n\n{message}")
        await update.message.reply_text("Отправлено.")
    except Exception as e:
        await update.message.reply_text(f"Не удалось отправить: {e}")


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
            ASK_GENDER: [CallbackQueryHandler(got_gender, pattern=f"^{GENDER_CB_PREFIX}")],
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
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )

    application.add_handler(TypeHandler(Update, restore_chart_if_needed), group=-2)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_intercept), group=-1)
    application.add_handler(conv)

    compat_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_compat, pattern=f"^{COMPAT_CB}$"),
            CallbackQueryHandler(payment_confirm_compat, pattern="^paycheck:compat$"),
        ],
        states={
            ASK_PARTNER_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_partner_date)],
            ASK_PARTNER_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_partner_time),
                CallbackQueryHandler(skip_partner_time, pattern=f"^{SKIP_PARTNER_TIME_CB}$"),
            ],
            ASK_PARTNER_CITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_partner_city),
                CallbackQueryHandler(skip_partner_city, pattern=f"^{SKIP_PARTNER_CITY_CB}$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(compat_conv)

    application.add_handler(CallbackQueryHandler(forecast, pattern=f"^{FORECAST_CB}$"))
    application.add_handler(CallbackQueryHandler(sphere, pattern=f"^{SPHERE_CB_PREFIX}"))
    application.add_handler(CallbackQueryHandler(unlived, pattern=f"^{UNLIVED_CB}$"))
    application.add_handler(CallbackQueryHandler(harmony, pattern=f"^{HARMONY_CB}$"))
    application.add_handler(CallbackQueryHandler(extended_natal, pattern=f"^{EXTENDED_NATAL_CB}$"))
    application.add_handler(CallbackQueryHandler(numerology, pattern=f"^{NUMEROLOGY_CB}$"))
    application.add_handler(CallbackQueryHandler(tomorrow_menu, pattern=f"^{TOMORROW_CB}$"))
    application.add_handler(CallbackQueryHandler(money_ritual, pattern=f"^{MONEY_RITUAL_CB}$"))
    application.add_handler(CallbackQueryHandler(tomorrow_western, pattern=f"^{TOMORROW_WESTERN_CB}$"))
    application.add_handler(CallbackQueryHandler(tomorrow_choghadiya, pattern=f"^{TOMORROW_CHOGHADIYA_CB}$"))
    application.add_handler(CallbackQueryHandler(payment_confirm, pattern="^paycheck:(numerology|tomorrow|money_ritual|extended_natal)$"))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("reply", reply_to_user))

    log.info("Небосвод запущен, жду сообщений…")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
