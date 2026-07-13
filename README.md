# notices bot

sends notices to discord and telegram

## setup

```
pip install -r requirements.txt
```

`.env`:
```
DISCORD_WEBHOOK1=your_webhook
DISCORD_WEBHOOK2=your_webhook
TELEGRAM_TOKEN=your_token
TELEGRAM_CHAT_ID=123456789
# or several chats:
# TELEGRAM_CHAT_ID=111,222
```

```
python bot.py
```

## telegram

Commands only:
- `/start` — menu (keyboard + buttons)
- `/post` — quick dump (old style)
- `/status` — active counts / due tomorrow / next reminder
- `/cancel` — abort current wizard or quick-post

Buttons: **Create / List / Edit / Delete / Status**

Edit title/body/deadline; optional **Update live posts** edits the same TG/Discord messages (does not spam a new one).
Delete removes JSON **and** the live TG/Discord messages when message IDs were saved at publish.

Reminders: **6:00 PM Nepal time** for assignments due **tomorrow** (combined message). Missed 6pm is not retried.

Storage: `manual_notices.json`, `scraped_notices.json`

## scrape

Still checks exam.ioe + tcioe about every 10 min, posts new notices, tags them `from_site`.

## deploy

see DEPLOY.md
