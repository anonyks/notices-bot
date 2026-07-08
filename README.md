# notices bot

sends notices to discord, telegram, and messenger

## setup

install:
```
pip install requests beautifulsoup4
```

make .env file:
```
DISCORD_WEBHOOK1=your_webhook
DISCORD_WEBHOOK2=your_webhook
TELEGRAM_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
FB_PAGE_TOKEN=your_fb_token (optional)
FB_RECIPIENT_ID=your_recipient_id (optional)
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

messenger (optional):
- create facebook page
- create app at developers.facebook.com
- add messenger product
- generate page access token
- get recipient PSID by messaging your page

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
