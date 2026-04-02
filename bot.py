import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")


async def start_handler(message: Message) -> None:
    if not WEBAPP_URL:
        await message.answer(
            "WEBAPP_URL не настроен. Добавь его в .env, например через ngrok."
        )
        return

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="Открыть мини-игру",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            ]
        ],
        resize_keyboard=True,
    )

    await message.answer(
        (
            "Это игра-фармилка.\n"
            "Скоро в ней можно будет получить криптотокен.\n\n"
            "Жми кнопку ниже и открывай мини-апп."
        ),
        reply_markup=keyboard,
    )


async def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("Переменная BOT_TOKEN не задана в .env")

    async with Bot(token=BOT_TOKEN) as bot:
        try:
            await bot.delete_webhook(drop_pending_updates=False)
        except TelegramNetworkError as e:
            print(
                "\nНе удалось подключиться к api.telegram.org (HTTPS).\n"
                "Частые причины: блокировка у провайдера, файрвол/антивирус, нет интернета.\n"
                "Попробуй VPN, другую сеть (мобильный интернет) или разреши Python в брандмауэре.\n"
                f"Детали: {e}\n",
                flush=True,
            )
            return

        dp = Dispatcher()
        dp.message.register(start_handler, CommandStart())
        print(
            "Бот запущен, long polling… Отправь /start в Telegram. Ctrl+C — стоп.",
            flush=True,
        )
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
