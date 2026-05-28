import asyncio
import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import Message


load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("archivebot")


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str
    archive_chat: str | int
    archive_command: str = "archive"


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _chat_id(value: str) -> str | int:
    return int(value) if value.lstrip("-").isdigit() else value


def load_settings() -> Settings:
    return Settings(
        api_id=int(_required("API_ID")),
        api_hash=_required("API_HASH"),
        bot_token=_required("BOT_TOKEN"),
        archive_chat=_chat_id(_required("ARCHIVE_CHAT")),
        archive_command=os.getenv("ARCHIVE_COMMAND", "archive").lstrip("/"),
    )


settings = load_settings()
app = Client(
    "archivebot",
    api_id=settings.api_id,
    api_hash=settings.api_hash,
    bot_token=settings.bot_token,
)


async def safe_reply(message: Message, text: str) -> None:
    try:
        await message.reply_text(text, quote=True)
    except RPCError:
        logger.exception("Could not send status reply")


async def copy_message_with_flood_wait(
    target_chat: str | int,
    source_chat: int | str,
    message_id: int,
) -> None:
    while True:
        try:
            await app.copy_message(
                chat_id=target_chat,
                from_chat_id=source_chat,
                message_id=message_id,
            )
            return
        except FloodWait as flood_wait:
            logger.warning("Flood wait for %s seconds", flood_wait.value)
            await asyncio.sleep(flood_wait.value)


async def archive_single_message(command: Message, replied: Message) -> int:
    await copy_message_with_flood_wait(
        settings.archive_chat,
        replied.chat.id,
        replied.id,
    )
    return 1


async def archive_media_group(command: Message, replied: Message) -> int:
    try:
        album = await app.get_media_group(replied.chat.id, replied.id)
    except RPCError:
        logger.exception("Could not fetch media group; falling back to one message")
        return await archive_single_message(command, replied)

    copied = 0
    for item in sorted(album, key=lambda message: message.id):
        await copy_message_with_flood_wait(
            settings.archive_chat,
            item.chat.id,
            item.id,
        )
        copied += 1
    return copied


@app.on_message(filters.group & filters.command(settings.archive_command))
async def archive_command(_: Client, message: Message) -> None:
    replied = message.reply_to_message
    if replied is None:
        await safe_reply(
            message,
            f"Reply to a message with /{settings.archive_command} to archive it.",
        )
        return

    try:
        if replied.media_group_id:
            count = await archive_media_group(message, replied)
        else:
            count = await archive_single_message(message, replied)
    except RPCError as error:
        logger.exception("Archive failed")
        await safe_reply(message, f"Archive failed: {error.MESSAGE}")
        return

    await safe_reply(message, f"Archived {count} message{'s' if count != 1 else ''}.")


if __name__ == "__main__":
    logger.info(
        "Starting archive bot with /%s -> %s",
        settings.archive_command,
        settings.archive_chat,
    )
    app.run()

