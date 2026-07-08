# deploy to render (free 24/7)

## 1. push to github

```bash
git remote add origin https://github.com/YOUR_USERNAME/ioe_result_bot.git
git push -u origin main
```

## 2. deploy

- go to render.com
- sign up (use github)
- new + > blueprint
- pick your repo
- click apply

## 3. add tokens

in render dashboard:
- environment tab
- add:
  - DISCORD_WEBHOOK1
  - DISCORD_WEBHOOK2
  - TELEGRAM_TOKEN
  - TELEGRAM_CHAT_ID
- save

done! bot runs 24/7

## check logs

logs tab in render

## restart

manual deploy > clear cache
