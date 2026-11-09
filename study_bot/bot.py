import asyncio
import base64
import io
import json
from pathlib import Path
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()
dp = Dispatcher()
histories = {}
access_file = Path(__file__).resolve().with_name("allowed_users.json")
allowed_users = set(json.loads(access_file.read_text()) if access_file.exists() else [])
instructions = (
    "Ты помощник по учёбе. Отвечай по-русски простым языком. "
    "Решай только указанные номера и объясняй шаги. "
    "Если условие на фото не читается, попроси более чёткое фото. "
    "Пиши обычным текстом для Telegram."
)


def is_owner(message: Message) -> bool:
    return (
        message.chat.type == "private"
        and message.from_user is not None
        and str(message.from_user.id) == os.getenv("TELEGRAM_OWNER_ID")
    )


def has_access(message: Message) -> bool:
    return is_owner(message) or (
        bool(os.getenv("TELEGRAM_OWNER_ID"))
        and message.chat.type == "private"
        and message.from_user is not None
        and message.from_user.id in allowed_users
    )


@dp.message(Command("allow", "deny"))
async def change_access(message: Message, command: CommandObject):
    if not is_owner(message):
        return
    value = (command.args or "").strip()
    if not value.isascii() or not value.isdecimal() or len(value) > 16 or int(value) == 0:
        await message.answer(f"Пример: /{command.command} 123456789")
        return

    user_id = int(value)
    if user_id == message.from_user.id:
        await message.answer("У владельца всегда есть доступ.")
        return
    updated = allowed_users.copy()
    if command.command == "allow":
        updated.add(user_id)
    else:
        updated.discard(user_id)

    temporary = access_file.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(sorted(updated)), encoding="utf-8")
        temporary.replace(access_file)
    except OSError:
        await message.answer("Не удалось сохранить список доступа.")
        return
    allowed_users.clear()
    allowed_users.update(updated)
    if command.command == "deny":
        histories.pop(user_id, None)
    action = "выдан" if command.command == "allow" else "отозван"
    await message.answer(f"Доступ {action} для ID {user_id}.")


@dp.message(Command("users"))
async def list_users(message: Message):
    if is_owner(message):
        result = "\n".join(map(str, sorted(allowed_users))) or "Список пуст."
        for offset in range(0, len(result), 1800):
            await message.answer(result[offset:offset + 1800])


@dp.message(Command("id"), F.chat.type == "private")
async def show_id(message: Message):
    await message.answer(f"Твой Telegram ID: {message.from_user.id}")


@dp.message(Command("start"))
async def start(message: Message):
    if has_access(message):
        await message.answer("Пришли вопрос или фото задания. /reset очистит диалог.")
    elif message.chat.type == "private":
        await message.answer("Отправь /id и передай число владельцу бота.")


@dp.message(Command("reset"))
async def reset(message: Message):
    if has_access(message):
        histories.pop(message.from_user.id, None)
        await message.answer("Диалог очищен.")


@dp.message(F.text | F.photo)
async def answer(message: Message, bot: Bot, client: AsyncOpenAI):
    if not has_access(message):
        return

    history = histories.setdefault(message.from_user.id, [])
    question = message.text or message.caption or "Реши задание на фото по шагам."
    content = [{"type": "input_text", "text": question}]
    try:
        if message.photo:
            photo = io.BytesIO()
            await bot.download(message.photo[-1], destination=photo)
            image = base64.b64encode(photo.getvalue()).decode("ascii")
            content.append({
                "type": "input_image",
                "image_url": f"data:image/jpeg;base64,{image}",
                "detail": "high",
            })

        await bot.send_chat_action(message.chat.id, "typing")
        user_turn = {"role": "user", "content": content}
        response = await client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-6-astra"),
            instructions=instructions,
            input=[*history, user_turn],
            store=False,
        )
        result = response.output_text.strip()
        if not result:
            await message.answer("Ответ не получен. Попробуй уточнить вопрос.")
            return
    except Exception as exc:
        logging.warning("Request failed: %s, status=%s",
                        type(exc).__name__, getattr(exc, "status_code", None))
        await message.answer("Не удалось обработать запрос. Проверь журнал запуска.")
        return

    for offset in range(0, len(result), 1800):
        await message.answer(result[offset:offset + 1800])
    history.extend([user_turn, {"role": "assistant", "content": result}])
    del history[:-8]


async def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Укажи TELEGRAM_BOT_TOKEN в .env")
    # Режим первоначальной настройки позволяет узнать ID без ключа OpenAI.
    if not os.getenv("TELEGRAM_OWNER_ID"):
        print("Режим настройки. Отправь боту /id, затем заполни TELEGRAM_OWNER_ID.")
        await dp.start_polling(Bot(token), handle_as_tasks=False, client=None)
        return
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("Укажи OPENAI_API_KEY в .env")
    async with AsyncOpenAI(timeout=90.0) as client:
        await dp.start_polling(Bot(token), handle_as_tasks=False, client=client)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
