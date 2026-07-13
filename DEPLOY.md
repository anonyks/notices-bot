# deploy to render (free)

## 1. push to github

```bash
git push -u origin main
```

## 2. deploy

- render.com → New → Blueprint (or Web Service from repo)
- use this repo + `render.yaml` / Dockerfile

## 3. environment

Add these in Render → Environment:

- `DISCORD_WEBHOOK1`
- `DISCORD_WEBHOOK2`
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID` — one id, or several: `111,222,333`

Deps (including `nepali-datetime`) install from `requirements.txt` automatically.

## 4. after deploy

Message the bot `/start` — you should get the menu keyboard.

## logs / restart

- Logs tab on Render
- Manual Deploy → clear build cache & deploy if needed
