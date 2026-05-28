# Archive Bot

A small Telegram bot built with Pyrofork. Add it to a group, reply to a message with `/archive`, and it copies the replied message to an archive channel. If the replied message is part of a media album, the bot tries to copy the whole album. You can also use `/archive <message>` to append a searchable follow-up note after the archived post in the destination channel.

## Setup

1. Create a bot with BotFather and add it to your group.
2. Add the bot to the archive channel as an admin with permission to post messages.
3. Copy `.env.example` to `.env` and fill in the values.
4. Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

5. Run the bot:

```bash
python bot.py
```

## Notes

- `API_ID` and `API_HASH` come from <https://my.telegram.org/apps>.
- `ARCHIVE_CHAT` can be a public channel username such as `@my_archive` or a numeric chat ID such as `-1001234567890`.
- For a private channel, prefer the numeric `-100...` channel ID. A plain positive ID like `3812511960` is not a valid target chat ID for this bot.
- In BotFather, disable group privacy if you want the bot to reliably see all album messages in the group. Reply-based archiving can work with privacy enabled, but full album lookup is more reliable when the bot can see group messages.
- Telegram only allows bots to copy messages they can access. The bot must be present in the source group and allowed to post in the target channel.
