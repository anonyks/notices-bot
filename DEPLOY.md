# Deploy to Render.com (Free 24/7)

## Quick Setup (5 minutes)

### 1. Push to GitHub
```bash
cd /Users/admin/Documents/github/ioe_result_bot
git init
git add .
git commit -m "IOE Result Bot"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/ioe_result_bot.git
git push -u origin main
```

### 2. Deploy on Render
1. Go to https://render.com
2. Sign up (free, use GitHub)
3. Click "New +" → "Blueprint"
4. Connect your GitHub repo `ioe_result_bot`
5. Render auto-detects `render.yaml`
6. Click "Apply"

### 3. Add Environment Variables
In Render dashboard:
1. Go to your service
2. Click "Environment"
3. Add these:
   - `DISCORD_WEBHOOK1` = your webhook URL
   - `DISCORD_WEBHOOK2` = your webhook URL
   - `TELEGRAM_TOKEN` = your bot token
   - `TELEGRAM_CHAT_ID` = your chat ID

4. Click "Save Changes"
5. Bot auto-redeploys!

### Done! ✅
Bot runs 24/7 for free (750 hours/month = 31 days)

## Check Logs
- Click "Logs" tab in Render dashboard
- See bot output live

## Restart Bot
- Click "Manual Deploy" → "Clear build cache & deploy"

## Local Testing First
```bash
python3 bot.py
```
Make sure it works before deploying!
