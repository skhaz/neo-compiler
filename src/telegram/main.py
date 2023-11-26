import json
import os

from google.cloud import pubsublite_v1
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from telegram import Update
from telegram.ext import Application
from telegram.ext import CommandHandler
from telegram.ext import ContextTypes

application = (
    Application.builder().token(os.environ["TELEGRAM_TOKEN"]).updater(None).build()
)

pubsub = pubsublite_v1.PublisherServiceAsyncClient()


async def on_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return

    text = message.text
    if not text:
        return

    text = text.lstrip("/run")

    if not text:
        await message.reply_text("Luke, I need the code for the Death Star's system.")
        return

    payload = {
        "message": {
            "chat": {
                "id": message.chat.id,
            },
            "message": {
                "id": message.message_id,
            },
            "source": text,
        }
    }

    data = json.dumps(payload).encode("utf-8")

    request = pubsublite_v1.PublishRequest(topic=os.environ["TOPIC"], messages=[{"data": data}])

    async def request_generator():
        yield request

    await pubsub.publish(requests=request_generator())

    await message.reply_text("Ok")

    # stream = await pubsub.publish(requests=request_generator())

    # async for response in stream:
    #     print(f"{response.message_ids}")


def equals(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False

    if len(left) != len(right):
        return False

    for c1, c2 in zip(left, right):
        if c1 != c2:
            return False

    return True


async def webhook(request: Request):
    if not equals(
        request.headers.get("X-Telegram-Bot-Api-Secret-Token"),
        os.environ["SECRET"],
    ):
        print("Unauthorized")
        return Response(content="Unauthorized", status_code=401)

    payload = await request.json()

    print(payload)

    async with application:
        await application.process_update(Update.de_json(payload, application.bot))

    return Response(status_code=200)


application.add_handler(CommandHandler("run2", on_run))

app = Starlette(
    debug=True,
    routes=[
        Route("/", webhook, methods=["POST"]),
    ],
)