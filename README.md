# notices bot

sends notices to discord and telegram

## setup

install:
```
pip install -r requirements.txt
```

make .env file:
```
DISCORD_WEBHOOK1=your_webhook
DISCORD_WEBHOOK2=your_webhook
TELEGRAM_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

run:
```
python bot.py
```

## get tokens

discord webhook:
- server settings > integrations > webhooks > new
- copy url

telegram:
- message @BotFather
- /newbot
- copy token
- message your bot
- go to: api.telegram.org/botTOKEN/getUpdates
- copy chat id

## commands

send to telegram bot:
- /start
- /status
- /latest
- /stop
- /post your message
- send pdf/image

## deploy free

see DEPLOY.md

## what it does

checks exam.ioe.tu.edu.np and tcioe.edu.np every 5 min
posts new notices
sends pdfs
wont spam old stuff
