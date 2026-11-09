import os

from aiohttp import web
from aiogram import Bot
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from openai import AsyncOpenAI

from bot import dp

WEBHOOK_PATH = "/telegram"

token = os.environ["TELEGRAM_BOT_TOKEN"]
secret = os.environ["TELEGRAM_WEBHOOK_SECRET"]
public_url = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
api_key = os.getenv("OPENAI_API_KEY")

bot = Bot(token)
client = AsyncOpenAI(api_key=api_key, timeout=90.0) if api_key else None


async def register_webhook(bot: Bot):
    if os.getenv("TELEGRAM_OWNER_ID") and client is None:
        raise RuntimeError("Укажи OPENAI_API_KEY в настройках Render")

    await bot.set_webhook(
        url=public_url + WEBHOOK_PATH,
        secret_token=secret,
        allowed_updates=dp.resolve_used_update_types(),
    )


async def health(request):
    return web.Response(text="OK")


async def close_client(app):
    if client is not None:
        await client.close()


dp.startup.register(register_webhook)

app = web.Application()
app.router.add_get("/", health)
app.on_cleanup.append(close_client)

SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
    secret_token=secret,
    client=client,
).register(app, path=WEBHOOK_PATH)

setup_application(app, dp, bot=bot)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
