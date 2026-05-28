import asyncio
import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, PeerIdInvalid, RPCError
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
) -> Message:
    while True:
        try:
            return await app.copy_message(
                chat_id=target_chat,
                from_chat_id=source_chat,
                message_id=message_id,
            )
        except FloodWait as flood_wait:
            logger.warning("Flood wait for %s seconds", flood_wait.value)
            await asyncio.sleep(flood_wait.value)


async def copy_media_group_with_flood_wait(
    target_chat: str | int,
    source_chat: int | str,
    message_id: int,
) -> list[Message]:
    while True:
        try:
            return await app.copy_media_group(
                chat_id=target_chat,
                from_chat_id=source_chat,
                message_id=message_id,
            )
        except FloodWait as flood_wait:
            logger.warning("Flood wait for %s seconds", flood_wait.value)
            await asyncio.sleep(flood_wait.value)


async def latest_chat_message(chat_id: str | int) -> Message | None:
    async for item in app.get_chat_history(chat_id, limit=1):
        return item
    return None


async def recover_copied_media_group(target_chat: str | int) -> list[Message]:
    latest_message = await latest_chat_message(target_chat)
    if latest_message is None:
        raise RuntimeError("Could not find the newly archived media group")

    if latest_message.media_group_id:
        return await app.get_media_group(target_chat, latest_message.id)

    return [latest_message]


def archive_chat_help_text() -> str:
    return (
        "Archive target is not reachable. Check ARCHIVE_CHAT and make sure the bot "
        "has already been added to that channel as an admin. For private channels, "
        "use the channel ID in the form -1001234567890."
    )


def chat_display_name(chat_id: int | str, title: str | None) -> str:
    return title or str(chat_id)


def telegram_message_link(message: Message) -> str | None:
    chat = message.chat
    if chat.username:
        return f"https://t.me/{chat.username}/{message.id}"

    if isinstance(chat.id, int):
        private_chat_id = str(chat.id)
        if private_chat_id.startswith("-100"):
            return f"https://t.me/c/{private_chat_id[4:]}/{message.id}"

    return None


async def archive_single_message(command: Message, replied: Message) -> Message:
    return await copy_message_with_flood_wait(
        settings.archive_chat,
        replied.chat.id,
        replied.id,
    )


async def archive_media_group(command: Message, replied: Message) -> list[Message]:
    try:
        return await copy_media_group_with_flood_wait(
            settings.archive_chat,
            replied.chat.id,
            replied.id,
        )
    except TypeError:
        logger.exception("copy_media_group returned an invalid response; recovering")
        return await recover_copied_media_group(settings.archive_chat)
    except RPCError:
        logger.exception("Could not fetch media group; falling back to one message")
        return [await archive_single_message(command, replied)]


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
            copied_messages = await archive_media_group(message, replied)
        else:
            copied_messages = [await archive_single_message(message, replied)]
    except PeerIdInvalid:
        logger.exception("Archive target is invalid or unknown")
        await safe_reply(message, archive_chat_help_text())
        return
    except RPCError as error:
        logger.exception("Archive failed")
        await safe_reply(message, f"Archive failed: {error.MESSAGE}")
        return

    if len(copied_messages) == 1:
        archived_message = copied_messages[0]
        channel_name = chat_display_name(
            archived_message.chat.id,
            archived_message.chat.title,
        )
        message_link = telegram_message_link(archived_message)
        reply_text = (
            f"Archived 1 message in channel {channel_name}"
            if message_link is None
            else f"Archived 1 message in channel {channel_name}: {message_link}"
        )
        await safe_reply(
            message,
            reply_text,
        )
        return

    channel_name = chat_display_name(
        copied_messages[0].chat.id,
        copied_messages[0].chat.title,
    )
    await safe_reply(
        message,
        f"Archived {len(copied_messages)} messages in channel {channel_name}.",
    )


if __name__ == "__main__":
    logger.info(
        "Starting archive bot with /%s -> %s",
        settings.archive_command,
        settings.archive_chat,
    )
    app.run()
