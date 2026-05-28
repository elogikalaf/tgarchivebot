import asyncio
import inspect
import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv
from pyrogram import Client, filters, raw
from pyrogram.errors import FloodWait, PeerIdInvalid, RPCError
from pyrogram.types import Message


load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("archivebot")


def patch_raw_messages_topics_default() -> None:
    messages_type = raw.types.messages.Messages
    signature = inspect.signature(messages_type)
    topics = signature.parameters.get("topics")
    if topics is None or topics.default is not inspect.Parameter.empty:
        return

    original_init = messages_type.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("topics", [])
        original_init(self, *args, **kwargs)

    messages_type.__init__ = patched_init


patch_raw_messages_topics_default()


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


def archive_note_text(message: Message) -> str | None:
    if not message.text:
        return None

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return None

    note = parts[1].strip()
    return note or None


async def send_archive_note(
    target_chat: str | int,
    reply_to_message_id: int,
    text: str,
) -> Message:
    while True:
        try:
            return await app.send_message(
                chat_id=target_chat,
                text=text,
                reply_to_message_id=reply_to_message_id,
            )
        except FloodWait as flood_wait:
            logger.warning("Flood wait for %s seconds", flood_wait.value)
            await asyncio.sleep(flood_wait.value)


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
    except RPCError:
        logger.exception("Could not fetch media group; falling back to one message")
        return [await archive_single_message(command, replied)]


async def archive_note_if_present(
    command: Message,
    copied_messages: list[Message],
) -> Message | None:
    note = archive_note_text(command)
    if note is None or not copied_messages:
        return None

    return await send_archive_note(
        settings.archive_chat,
        copied_messages[-1].id,
        note,
    )


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

        note_message = await archive_note_if_present(message, copied_messages)
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
        if note_message is not None:
            reply_text += " Added search note after the archived message."
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
        (
            f"Archived {len(copied_messages)} messages in channel {channel_name}. "
            "Added search note after the archived media group."
            if note_message is not None
            else f"Archived {len(copied_messages)} messages in channel {channel_name}."
        ),
    )


if __name__ == "__main__":
    logger.info(
        "Starting archive bot with /%s -> %s",
        settings.archive_command,
        settings.archive_chat,
    )
    app.run()
