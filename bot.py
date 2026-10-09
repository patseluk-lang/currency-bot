"""Telegram currency converter bot powered by the Monobank public API.

The bot converts Ukrainian hryvnia (UAH) into a set of popular currencies
using live rates. User-facing messages are in Ukrainian; code and comments
are in English.
"""

import json
import logging
import math
import os
import time

from datetime import datetime

import requests
import telebot
from telebot import types

# Configuration

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is not set. "
        "Set it before starting the bot: export BOT_TOKEN='your_token'"
    )

MONOBANK_API = "https://api.monobank.ua/bank/currency"
HISTORY_FILE = "history.json"

MAX_HISTORY = 10          # entries kept per user
REQUEST_TIMEOUT = 10      # seconds
RATES_TTL = 60            # seconds; Monobank allows one request per minute

UAH_CODE = 980

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN)

# Currencies

CURRENCIES = {
    "USD": 840,
    "EUR": 978,
    "GBP": 826,
    "CHF": 756,
    "PLN": 985,
    "CZK": 203,
    "UAH": UAH_CODE,
}

# Per-chat conversation state: {chat_id: {"step": ..., "amount": ...}}
user_state = {}

# Rates cache
# Monobank rejects requests made more often than once per 60 seconds,
# so responses are cached and reused within that window.

_rates_cache = {"data": None, "fetched_at": 0.0}


def fetch_rates() -> list | None:
    """Return the Monobank rate table, using a cached copy when it is fresh."""
    now = time.monotonic()
    if _rates_cache["data"] is not None and now - _rates_cache["fetched_at"] < RATES_TTL:
        return _rates_cache["data"]

    try:
        response = requests.get(MONOBANK_API, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        rates = response.json()
    except requests.RequestException as error:
        logger.warning("Monobank request failed: %s", error)
        return _rates_cache["data"]  # fall back to the last known table, if any
    except ValueError as error:
        logger.warning("Monobank returned malformed JSON: %s", error)
        return _rates_cache["data"]

    _rates_cache["data"] = rates
    _rates_cache["fetched_at"] = now
    return rates


# Rate lookup


def get_rate(from_code: int, to_code: int) -> float | None:
    """Return how many units of `to_code` one unit of `from_code` is worth.

    Monobank publishes `rateBuy` and `rateSell` only for the most traded
    currencies; the rest carry `rateCross` alone, so every branch falls back
    to the cross rate before giving up.
    """
    if from_code == to_code:
        return 1.0

    rates = fetch_rates()
    if not rates:
        return None

    for rate in rates:
        code_a = rate.get("currencyCodeA")
        code_b = rate.get("currencyCodeB")
        buy = rate.get("rateBuy") or 0
        sell = rate.get("rateSell") or 0
        cross = rate.get("rateCross") or 0

        # CURRENCY -> UAH: the bank buys the currency at `rateBuy`.
        if code_a == from_code and code_b == UAH_CODE and to_code == UAH_CODE:
            return buy or cross or None

        # UAH -> CURRENCY: the bank sells the currency at `rateSell`.
        # The value returned is the UAH price of one unit of the currency.
        if code_a == to_code and code_b == UAH_CODE and from_code == UAH_CODE:
            return sell or cross or None

        # Direct pair between two foreign currencies.
        if code_a == from_code and code_b == to_code:
            return cross or sell or None

        # Same pair listed in reverse order.
        if code_a == to_code and code_b == from_code:
            reverse = cross or buy or None
            return (1 / reverse) if reverse else None

    return None


def convert_amount(amount: float, from_code: int, to_code: int) -> float | None:
    """Convert `amount` between two currencies, routing through UAH if needed."""
    if from_code == to_code:
        return amount

    if from_code == UAH_CODE:
        # `sell` is the UAH price of one unit of the target currency.
        sell = get_rate(UAH_CODE, to_code)
        if sell:
            return amount / sell

    elif to_code == UAH_CODE:
        # `buy` is how many UAH the bank gives for one unit of the currency.
        buy = get_rate(from_code, UAH_CODE)
        if buy:
            return amount * buy

    else:
        cross = get_rate(from_code, to_code)
        if cross:
            return amount * cross

    # Fallback: convert through UAH as an intermediate currency.
    source_in_uah = get_rate(from_code, UAH_CODE)
    target_in_uah = get_rate(UAH_CODE, to_code)

    if source_in_uah and target_in_uah:
        return (amount * source_in_uah) / target_in_uah

    return None


# History storage
# Stored as {chat_id: [entry, ...]} so that users never see each other's data.


def _read_history_file() -> dict:
    """Load the whole history file, tolerating a missing or damaged file."""
    if not os.path.exists(HISTORY_FILE):
        return {}

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError) as error:
        logger.warning("Could not read %s: %s", HISTORY_FILE, error)
        return {}

    # Files written by the earlier single-user version held a plain list.
    if not isinstance(data, dict):
        return {}

    return data


def load_history(chat_id: int) -> list:
    """Return the conversion history of one chat, oldest entry first."""
    return _read_history_file().get(str(chat_id), [])


def save_to_history(chat_id: int, entry: dict) -> None:
    """Append one conversion to a chat's history, keeping the last N entries."""
    data = _read_history_file()
    key = str(chat_id)

    entries = data.get(key, [])
    entries.append(entry)
    data[key] = entries[-MAX_HISTORY:]

    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
    except OSError as error:
        logger.warning("Could not write %s: %s", HISTORY_FILE, error)


# Keyboards


def currency_keyboard() -> types.InlineKeyboardMarkup:
    """Inline keyboard listing every target currency except UAH."""
    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = [
        types.InlineKeyboardButton(name, callback_data=f"currency_{name}")
        for name in CURRENCIES
        if name != "UAH"
    ]
    markup.add(*buttons)
    return markup


def main_menu_keyboard() -> types.ReplyKeyboardMarkup:
    """Persistent reply keyboard shown under the input field."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("💱 Конвертувати", "📋 Історія")
    markup.add("ℹ️ Допомога")
    return markup


# Command handlers


@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    """Greet the user and explain how the bot works."""
    name = message.from_user.first_name
    text = (
        f"👋 Привіт, *{name}*!\n\n"
        "Я — бот-конвертер валют. Допоможу швидко дізнатись\n"
        "актуальний курс і конвертувати суму в потрібну валюту.\n\n"
        "📌 *Як користуватися:*\n"
        "1️⃣ Натисни *«💱 Конвертувати»*\n"
        "2️⃣ Введи суму в гривнях (наприклад: `500`)\n"
        "3️⃣ Обери цільову валюту\n"
        "4️⃣ Отримай результат 🎉\n\n"
        "Усі курси беруться з *Monobank API* в реальному часі."
    )
    bot.send_message(
        message.chat.id,
        text,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )


@bot.message_handler(func=lambda m: m.text == "ℹ️ Допомога")
def handle_help_button(message):
    """Reuse the greeting text for the help button."""
    handle_start(message)


@bot.message_handler(func=lambda m: m.text == "📋 Історія")
def handle_history(message):
    """Show the last conversions made in this chat, newest first."""
    history = load_history(message.chat.id)
    if not history:
        bot.send_message(message.chat.id, "📭 Історія конвертацій порожня.")
        return

    lines = [f"📋 *Останні {MAX_HISTORY} конвертацій:*\n"]
    for index, entry in enumerate(reversed(history), start=1):
        lines.append(
            f"{index}. `{entry['amount']} {entry['from']}` → "
            f"`{entry['result']:.4f} {entry['to']}` — {entry['date']}"
        )

    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text == "💱 Конвертувати")
def handle_convert_start(message):
    """Start the conversion flow by asking for an amount."""
    user_state[message.chat.id] = {"step": "waiting_amount"}
    bot.send_message(
        message.chat.id,
        "💵 Введи суму в *гривнях (UAH)*, яку хочеш конвертувати:\n"
        "_(наприклад: `1000`)_",
        parse_mode="Markdown",
    )


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "waiting_amount"
)
def handle_amount_input(message):
    """Validate the amount and offer the currency keyboard."""
    raw = (message.text or "").strip().replace(",", ".").replace(" ", "")

    try:
        amount = float(raw)
    except ValueError:
        bot.send_message(message.chat.id, "⚠️ Введи коректне число більше нуля.")
        return

    if not math.isfinite(amount) or amount <= 0:
        bot.send_message(message.chat.id, "⚠️ Введи коректне число більше нуля.")
        return

    user_state[message.chat.id] = {"step": "waiting_currency", "amount": amount}
    bot.send_message(
        message.chat.id,
        f"✅ Сума: *{amount} UAH*\n\nОбери цільову валюту:",
        parse_mode="Markdown",
        reply_markup=currency_keyboard(),
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("currency_"))
def handle_currency_choice(call):
    """Perform the conversion once a target currency is picked."""
    chat_id = call.message.chat.id
    state = user_state.get(chat_id)

    if not state or state.get("step") != "waiting_currency":
        bot.answer_callback_query(call.id, "Спочатку введи суму.")
        return

    target_currency = call.data.removeprefix("currency_")
    if target_currency not in CURRENCIES:
        bot.answer_callback_query(call.id, "Невідома валюта.")
        return

    amount = state["amount"]

    bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    bot.answer_callback_query(call.id, f"Конвертую в {target_currency}...")

    result = convert_amount(amount, UAH_CODE, CURRENCIES[target_currency])

    if result is None:
        bot.send_message(chat_id, "❌ Не вдалося отримати курс. Спробуй пізніше.")
        user_state.pop(chat_id, None)
        return

    entry = {
        "amount": amount,
        "from": "UAH",
        "to": target_currency,
        "result": round(result, 4),
        "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
    }
    save_to_history(chat_id, entry)

    text = (
        "✅ *Результат конвертації:*\n\n"
        f"💰 `{amount} UAH` = *`{result:.4f} {target_currency}`*\n\n"
        f"📅 {entry['date']}"
    )
    bot.send_message(
        chat_id,
        text,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    user_state.pop(chat_id, None)


@bot.message_handler(func=lambda m: True)
def handle_unknown(message):
    """Catch anything that no other handler claimed."""
    bot.send_message(
        message.chat.id,
        "❓ Не розумію цю команду. Скористайся меню нижче.",
        reply_markup=main_menu_keyboard(),
    )


# Entry point

if __name__ == "__main__":
    logger.info("Bot started, waiting for messages...")
    bot.infinity_polling()
