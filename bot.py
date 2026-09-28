import asyncio
import logging
import os
import re
import uuid
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import (
    ChosenInlineResult,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from supabase import Client, create_client

# Твои реальные данные
API_TOKEN = "8821176519:AAElZZGxZe4ApnDCshtHB--_wmzwGIiT5AU"
SUPABASE_URL = "https://icfaxszodkpqqmkgvbbv.supabase.co"
SUPABASE_KEY = "sb_publishable_rtdALSldJ8O17Fm7RfTzZQ_e4kuWqPL"

# Инициализация базы данных (работает в фоне)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Словарь для хранения последней операции (на случай отмены без реплая)
user_last_action = {}


def get_user_balance(user_id: int):
  """Получает баланс из базы данных. Если пользователя нет — создает строку с нулями."""
  response = (
      supabase.table("user_balances")
      .select("usd, uah")
      .eq("user_id", user_id)
      .execute()
  )

  if response.data:
    return {
        "USD": float(response.data[0]["usd"]),
        "UAH": float(response.data[0]["uah"]),
    }
  else:
    initial_data = {"user_id": user_id, "usd": 0.0, "uah": 0.0}
    supabase.table("user_balances").insert(initial_data).execute()
    return {"USD": 0.0, "UAH": 0.0}


def save_user_balance(user_id: int, usd: float, uah: float):
  """Сохраняет актуальный баланс в базу данных"""
  supabase.table("user_balances").upsert(
      {"user_id": user_id, "usd": usd, "uah": uah}
  ).execute()


def parse_expression(text: str):
  """Универсальная функция парсинга суммы без изменения базы данных"""
  text = text.strip().lower().replace(",", ".")

  pattern = r"([+-])\s*([\d.]+)\s*(usd|\$|грн|uah|гривень|гривен|долларов|долл)?|([\d.]+)\s*(usd|\$|грн|uah|гривень|гривен|долларов|долл)"
  match = re.search(pattern, text)

  if not match:
    return None

  g1, g2, g3, g4, g5 = match.groups()

  if g1:
    sign = g1
    amount = float(g2)
    currency_raw = g3
  else:
    sign = "+"
    amount = float(g4)
    currency_raw = g5

  if currency_raw in ["usd", "$", "долларов", "долл"]:
    currency = "USD"
  else:
    currency = "UAH"

  action = "Списано" if sign == "-" else "Додано"
  return action, amount, currency, sign


def parse_and_update(user_id: int, text: str):
  """Парсит текст и сразу обновляет баланс в базе данных"""
  parsed = parse_expression(text)
  if not parsed:
    return None

  action, amount, currency, sign = parsed
  balances = get_user_balance(user_id)

  if sign == "-":
    balances[currency] -= amount
  else:
    balances[currency] += amount

  save_user_balance(user_id, balances["USD"], balances["UAH"])

  # Сохраняем последнюю операцию для команды /cancel без реплая
  user_last_action[user_id] = {
      "currency": currency,
      "sign": sign,
      "amount": amount,
  }

  return action, amount, currency, balances


@dp.message(Command("start"))
async def cmd_start(message: Message):
  text = (
      "🧮 **Зручний калькулятор** для обліку та розрахунку доходів і прибутку."
      " від продажу товарів чи послуг у USD та UAH.\n\n"
      "• Автоматично зчитує суми з тексту та фото\n"
      "• Працює в інлайн-режимі в будь-яких чатах\n"
      "• Надійний облік без зайвих сповіщень\n\n"
      "📌 **Команди керування:**\n"
      "/balance — переглянути поточний баланс\n"
      "/cancel — скасувати останню дію або дію з повідомлення у відповідь\n"
      "/reset — скинути баланс\n\n"
      "Создатель бота @SNOWyouNo"
  )
  await message.answer(text, parse_mode="Markdown")


@dp.message(Command("balance"))
async def cmd_balance(message: Message):
  balances = get_user_balance(message.from_user.id)
  response = (
      f"💰 **Твій поточний баланс:**\n\n"
      f"🇺🇸 USD: `{balances['USD']:.2f}`\n"
      f"🇺🇦 UAH: `{balances['UAH']:.2f}`"
  )
  await message.answer(response, parse_mode="Markdown")


@dp.message(Command("reset"))
async def cmd_reset(message: Message):
  save_user_balance(message.from_user.id, 0.0, 0.0)
  user_last_action.pop(message.from_user.id, None)
  await message.answer("🔄 Баланс успішно скинуто до нуля!")


@dp.message(Command("cancel", "отмена"))
async def cmd_cancel(message: Message):
  user_id = message.from_user.id

  # Режим 1: Отмена через ответ на абсолютно любое сообщение (с суммой +/-)
  if message.reply_to_message:
    reply_msg = message.reply_to_message
    reply_text = reply_msg.text or reply_msg.caption or ""

    parsed = parse_expression(reply_text)
    if parsed:
      _, amount, currency, sign = parsed
      balances = get_user_balance(user_id)

      # Если в сообщении был плюс, то при отмене вычитаем. Если минус — добавляем.
      if sign == "+":
        balances[currency] -= amount
      else:
        balances[currency] += amount

      save_user_balance(user_id, balances["USD"], balances["UAH"])
      await message.answer(
          f"❌ **Скасовано суму з повідомлення:** `{sign}{amount}"
          f" {currency}`\n\n💰 **Поточний баланс:**\n🇺🇸 USD:"
          f" `{balances['USD']:.2f}`\n🇺🇦 UAH: `{balances['UAH']:.2f}`",
          parse_mode="Markdown",
      )
      return
    else:
      await message.answer(
          "❌ У цьому повідомленні не знайдено суми для скасування (має бути"
          " знак + або - та число).",
          parse_mode="Markdown",
      )
      return

  # Режим 2: Отмена последней операции без реплая
  if user_id not in user_last_action:
    await message.answer(
        "❌ Немає останніх дій для скасування. Або відповідьте командою /cancel"
        " на будь-яке повідомлення з сумою.",
        parse_mode="Markdown",
    )
    return

  last = user_last_action[user_id]
  currency = last["currency"]
  sign = last["sign"]
  amount = last["amount"]

  balances = get_user_balance(user_id)
  if sign == "+":
    balances[currency] -= amount
  else:
    balances[currency] += amount

  save_user_balance(user_id, balances["USD"], balances["UAH"])
  del user_last_action[user_id]

  await message.answer(
      f"❌ **Останню дію скасовано!** (Скасовано: {sign}{amount}"
      f" {currency})\n\n💰 **Поточний баланс:**\n🇺🇸 USD:"
      f" `{balances['USD']:.2f}`\n🇺🇦 UAH:`{balances['UAH']:.2f}`",
      parse_mode="Markdown",
  )


# Обработка текстовых сообщений
@dp.message(F.text)
async def calculate_message(message: Message):
  text = message.text.strip()

  if "+" not in text and "-" not in text:
    return

  result = parse_and_update(message.from_user.id, text)
  if not result:
    await message.answer(
        "❌ Не зрозумів формат суми. Приклад: `Продаж +37$` або `-100 грн`",
        parse_mode="Markdown",
    )
    return

  action, amount, currency, balances = result
  response = (
      f"✅ {action}: `{amount:.2f} {currency}`\n\n"
      f"💰 **Поточний баланс:**\n"
      f"🇺🇸 USD: `{balances['USD']:.2f}`\n"
      f"🇺🇦 UAH: `{balances['UAH']:.2f}`"
  )
  await message.answer(response, parse_mode="Markdown")


# Обработка фотографий
@dp.message(F.photo)
async def calculate_photo(message: Message):
  caption = message.caption

  if not caption or ("+" not in caption and "-" not in caption):
    return

  result = parse_and_update(message.from_user.id, caption)
  if not result:
    await message.answer(
        "❌ Не зрозумів формат підпису. Приклад: `Товар +200 грн`",
        parse_mode="Markdown",
    )
    return

  action, amount, currency, balances = result
  response = (
      f"📸 Фото враховано! {action}: `{amount:.2f} {currency}`\n\n"
      f"💰 **Поточний баланс:**\n"
      f"🇺🇸 USD: `{balances['USD']:.2f}`\n"
      f"🇺🇦 UAH: `{balances['UAH']:.2f}`"
  )
  await message.answer(response, parse_mode="Markdown")


# Инлайн-режим (предпросмотр без изменения базы)
@dp.inline_query()
async def inline_calc(query: InlineQuery):
  text = query.query.strip()
  user_id = query.from_user.id

  if not text:
    results = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title="Калькулятор доходів",
            description="Введіть суму, наприклад: +50$ або -200 грн",
            input_message_content=InputTextMessageContent(
                message_text="ℹ️ Введіть суму після імені бота, наприклад: `@імя_бота +50$`"
            ),
        )
    ]
    await query.answer(results, cache_time=1)
    return

  parsed = parse_expression(text)
  if not parsed:
    results = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title="Помилка формату",
            description="Приклад: +50$ або -100 грн",
            input_message_content=InputTextMessageContent(
                message_text=f"❌ Невірний формат в інлайн-запиті: `{text}`",
                parse_mode="Markdown",
            ),
        )
    ]
  else:
    action, amount, currency, sign = parsed
    balances = get_user_balance(user_id)
    proj_balances = balances.copy()

    if sign == "-":
      proj_balances[currency] -= amount
    else:
      proj_balances[currency] += amount

    msg_text = (
        f"✅ {action} (інлайн): `{amount:.2f} {currency}`\n\n"
        f"💰 **Баланс після операції:**\n"
        f"🇺🇸 USD: `{proj_balances['USD']:.2f}`\n"
        f"🇺🇦 UAH: `{proj_balances['UAH']:.2f}`"
    )

    undo_callback = f"undo_{currency}_{sign}_{amount}"
    markup = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="❌ Скасувати дію", callback_data=undo_callback
                )
            ]
        ]
    )

    results = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=f"Врахувати: {text} ({action} {amount} {currency})",
            description=(
                f"Прогноз USD: {proj_balances['USD']:.2f} | UAH:"
                f" {proj_balances['UAH']:.2f}"
            ),
            input_message_content=InputTextMessageContent(
                message_text=msg_text, parse_mode="Markdown"
            ),
            reply_markup=markup,
        )
    ]

  await query.answer(results, cache_time=1)


@dp.chosen_inline_result()
async def chosen_inline_calc(chosen_result: ChosenInlineResult):
  user_id = chosen_result.from_user.id
  query_text = chosen_result.query.strip()
  if query_text:
    parse_and_update(user_id, query_text)


@dp.callback_query(F.data.startswith("undo_"))
async def process_undo(callback: types.CallbackQuery):
  data_parts = callback.data.split("_")
  if len(data_parts) == 4:
    _, currency, sign, amount_str = data_parts
    try:
      amount = float(amount_str)
      user_id = callback.from_user.id

      balances = get_user_balance(user_id)
      if sign == "+":
        balances[currency] -= amount
      else:
        balances[currency] += amount

      save_user_balance(user_id, balances["USD"], balances["UAH"])

      if callback.inline_message_id:
        await bot.edit_message_text(
            inline_message_id=callback.inline_message_id,
            text=(
                f"❌ **Дію скасовано!**\n\n💰 **Поточний баланс:**\n🇺🇸 USD:"
                f" `{balances['USD']:.2f}`\n🇺🇦 UAH:"
                f" `{balances['UAH']:.2f}`"
            ),
            parse_mode="Markdown",
            reply_markup=None,
        )
      await callback.answer("Дію успішно скасовано!", show_alert=False)
    except Exception as e:
      logging.error(f"Помилка при скасуванні: {e}")
      await callback.answer("Не вдалося скасувати дію.", show_alert=True)
  else:
    await callback.answer("Помилка даних.", show_alert=True)


async def health_check(request):
  return web.Response(text="Calculator Bot is running!")


async def main():
  app = web.Application()
  app.router.add_get("/", health_check)
  runner = web.AppRunner(app)
  await runner.setup()

  port = int(os.environ.get("PORT", 8080))
  site = web.TCPSite(runner, "0.0.0.0", port)
  await site.start()

  print(f"Бот успішно запущений на порту {port}!")
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
