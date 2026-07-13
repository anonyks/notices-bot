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

### Keep notices after redeploy (important)

Render **free** wipes local files on every deploy/restart. Manual notices live in JSON, so they disappear unless you back them up.

**Option A — GitHub Gist (works on free):**

1. On github.com → Gist → create a **secret** gist
2. Filename: `bot_state.json`
3. Content: `{}`
4. Copy the gist id from the URL (`https://gist.github.com/you/<THIS_ID>`)
5. Create a GitHub token with **gist** scope
6. In Render env, set:
   - `GITHUB_TOKEN` = that token
   - `STATE_GIST_ID` = the gist id

The bot restores from the gist on boot and updates it whenever notices/`posted.txt` change.

**Option B — Persistent disk (paid Starter+):**

- Attach a disk mounted at `/data`
- Set `DATA_DIR=/data`

Deps (including `nepali-datetime`) install from `requirements.txt` automatically.

## 4. after deploy

Message the bot `/start` — you should get the menu keyboard.

Check logs for `[STORE] Gist backup enabled` (or the WARNING if gist env is missing).

## logs / restart

- Logs tab on Render
- Manual Deploy → clear build cache & deploy if needed
