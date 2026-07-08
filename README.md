# ioe result bot

watches ioe website and sends to discord and telegram when new notice comes

## how to use

### 1. install stuff

```bash
pip install -r requirements.txt
```

### 2. make .env file

copy this:
```bash
cp .env.example .env
```

then edit .env and put your tokens

### 3. run it

```bash
python bot.py
```

## getting tokens

### discord
1. go to https://discord.com/developers/applications
2. make new application
3. go to bot tab and make bot
4. copy token
5. enable intents (all of them)
6. go to oauth2 and select bot, then send messages
7. copy url and open it to add bot to server
8. right click channel and copy id

### telegram
1. search @BotFather on telegram
2. send /newbot
3. make name
4. copy token
5. message your bot
6. open this url in browser: https://api.telegram.org/botYOUR_TOKEN/getUpdates
7. find chat id number

## what it does

- checks exam.ioe.tu.edu.np and tcioe.edu.np every 5 min
- posts new notices to discord and telegram
- sends PDFs with the notice
- wont spam old notices on first run

## telegram bot commands

Send these to your bot:

- `/start` - Start bot and see commands
- `/status` - Check if notifications ON/OFF and total posted
- `/latest` - Get latest notice from both sources
- `/stop` - Stop notifications (use /start to resume)
- `/post {message}` - Send custom notice to Discord

**Send files:**
- Send PDF/image to bot → forwards to Discord
- Add caption for custom message

## deploy 24/7 (free)

Want bot running forever? Deploy to Render.com:

See [DEPLOY.md](DEPLOY.md) for full instructions (takes 5 min)

Quick steps:
1. Push code to GitHub
2. Connect to Render.com
3. Add environment variables
4. Done! Bot runs 24/7

## files

- bot.py = main code
- requirements.txt = dependencies
- .env.example = config template
- posted.txt = tracks posted notices
- Dockerfile = for deployment
- render.yaml = Render config

thats it!
