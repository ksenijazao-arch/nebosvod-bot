"""Удержание: «Ваша неделя по Луне» (джйотиш, гочара Луны от натальной Луны)
и мягкое напоминание о незавершённой оплате.

Гочара Луны: благоприятна в 1, 3, 6, 7, 10 и 11-м знаке от натальной (ведической)
Луны, 8-й знак — Чандраштама, 2,5 дня в месяц, когда в джйотиш не начинают важного."""
from datetime import datetime, timedelta, timezone

import astro_calc as ac

MSK = timezone(timedelta(hours=3))
WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря"]
GOOD = {1, 3, 6, 7, 10, 11}
HOUSE_NOTE = {
    1: "Луна в вашем знаке: вы на виду и в ресурсе",
    3: "смелость и лёгкость в общении",
    6: "силы справляться с делами и побеждать в спорах",
    7: "встречи, договорённости и партнёрство",
    10: "работа и дела идут в гору",
    11: "время исполнения желаний и приятных вестей",
}


def _jd(dt_utc):
    return ac.to_jd(dt_utc.year, dt_utc.month, dt_utc.day, dt_utc.hour, dt_utc.minute)


def _moon_sid(dt_utc):
    jd = _jd(dt_utc)
    return ac.sidereal_lon(ac.moon_longitude(jd), jd)


def _sign_idx(lon):
    return int(lon // 30) % 12


def _fmt(dt):
    m = dt.astimezone(MSK)
    return f"{WD[m.weekday()]} {m.day} {MONTHS_GEN[m.month - 1]} {m:%H:%M}"


def week_intervals(natal_sign, week_start_msk):
    """Отрезки недели (по часу) с номером дома Луны от натальной Луны."""
    start = week_start_msk.astimezone(timezone.utc)
    out, cur_h, seg_start = [], None, start
    for k in range(0, 7 * 24 + 1):
        t = start + timedelta(hours=k)
        h = (_sign_idx(_moon_sid(t)) - natal_sign) % 12 + 1
        if cur_h is None:
            cur_h = h
        elif h != cur_h or k == 7 * 24:
            # уточнить момент смены до минуты
            lo, hi = t - timedelta(hours=1), t
            if h != cur_h:
                for _ in range(7):
                    mid = lo + (hi - lo) / 2
                    if (_sign_idx(_moon_sid(mid)) - natal_sign) % 12 + 1 == cur_h:
                        lo = mid
                    else:
                        hi = mid
            out.append((seg_start, hi, cur_h))
            seg_start, cur_h = hi, h
    return out


def build_weekly(chart, week_start_msk):
    moon_natal = ac.sidereal_lon(ac.point_lon(chart["moon"]), chart["birth_jd"])
    natal_sign = _sign_idx(moon_natal)
    sign_name = ac.SIGNS[natal_sign] if hasattr(ac, "SIGNS") else None
    nak_natal = ac.nakshatra_of(moon_natal)["name"]
    segs = week_intervals(natal_sign, week_start_msk)
    end = week_start_msk + timedelta(days=6)
    head = f"🌙 Ваша неделя по Луне, {week_start_msk.day} — {end.day} {MONTHS_GEN[end.month - 1]}\n\n"
    intro = ("Это джйотиш-прогноз по вашей ведической Луне"
             + (f" ({ac.v_predlog(sign_name)} {ac.SIGN_PREPOSITIONAL[sign_name]})" if sign_name else "")
             + ". Каждые два с половиной дня Луна переходит в новый знак, и от того, где она стоит относительно "
               "вашей Луны, зависит, как легко идут дела и настроение.")
    w0, w1 = segs[0][0], segs[-1][1]

    def span(a, b):
        if a == w0 and b == w1:
            return "всю неделю"
        if a == w0:
            return f"до {_fmt(b)}"
        if b == w1:
            return f"с {_fmt(a)}"
        return f"с {_fmt(a)} до {_fmt(b)}"
    good = [f"• {span(a, b)} — {HOUSE_NOTE[h]}" for a, b, h in segs if h in GOOD]
    bad = [(a, b) for a, b, h in segs if h == 8]
    parts = [head + intro]
    if good:
        parts.append("✨ Лёгкие дни недели (время московское):\n" + "\n".join(good))
    if bad:
        a, b = bad[0]
        parts.append(
            f"⚠️ Чандраштама: {span(a, b)}. Луна в 8-м знаке от вашей. В джйотиш эти два с половиной дня "
            "не начинают важного, не подписывают договоры и не выясняют отношения: эмоции тоньше, ошибки дороже. "
            "Хорошее время отдыхать, доделывать и беречь силы."
        )
    else:
        parts.append("Чандраштамы, самых уязвимых дней месяца, на этой неделе у вас нет.")
    # дни вашей накшатры
    nak_days = []
    for d in range(7):
        t = (week_start_msk + timedelta(days=d, hours=12)).astimezone(timezone.utc)
        if ac.nakshatra_of(_moon_sid(t))["name"] == nak_natal:
            nak_days.append(week_start_msk + timedelta(days=d))
    if nak_days:
        d0 = nak_days[0]
        parts.append(f"🕉 {WD[d0.weekday()].capitalize()}, {d0.day} {MONTHS_GEN[d0.month - 1]} — день вашей накшатры {nak_natal}: "
                     "Луна возвращается туда, где стояла при вашем рождении. Хороший день, чтобы побыть с собой и загадать намерение на месяц.")
    parts.append("Хорошей недели! Подробный прогноз на любой день по вашей карте есть в меню бота.")
    return "\n\n".join(parts)


WHY = {
    "venus_retro": "Ретроградная Венера идёт по вашей карте до 14 ноября, и самые острые ваши дни ещё впереди. В разборе их точные даты и что делать на каждом этапе, чтобы не принять важное решение в неудачный момент.",
    "tomorrow": "Прогноз полезен именно накануне, пока завтрашний день ещё можно спланировать: какие часы удачны для важного, а какие лучше оставить для рутины.",
    "compat": "Совместимость показывает, где вы с партнёром усиливаете друг друга, а где нужна бережность. Это знание помогает не спорить там, где можно просто понять.",
    "numerology": "Число жизненного пути объясняет, какой урок проходит человек и в чём его опора. С ним легче понять, почему одни дела идут легко, а другие буксуют.",
    "money_ritual": "Ритуал подобран под вашу денежную планету, а не под знак. Делать его лучше всего в её день недели, и ближайший такой день уже скоро.",
    "extended_natal": "Расширенный разбор показывает все 13 точек карты, включая те, о которых первый разбор молчит: Лилит, Вертекс и Парс фортуны.",
    "compat_numerology": "Числовая формула пары показывает, ради чего вы встретились и какую задачу решаете вместе.",
    "compat_month": "Прогноз на месяц для пары подсказывает, в какие дни лучше договариваться, а в какие беречь друг друга от острых разговоров.",
    "matrix": "Матрица судьбы собирает ваши таланты, денежный канал и главную задачу воплощения в одну картину, к которой можно возвращаться годами.",
}


def reminder_text(label, feature=None):
    why = WHY.get(feature, "")
    return (f"🌙 Вчера вы почти открыли: {label}. Всё уже рассчитано по вашей карте и ждёт вас."
            + (f"\n\n{why}" if why else "")
            + "\n\nЕсли передумали, просто пропустите это сообщение, больше напоминать не буду.")
