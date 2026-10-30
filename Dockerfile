FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
# patchright (a stealth-patched Chromium) is only used to pick up tcioe's
# Cloudflare clearance cookie, not launched on every request - see bot.py.
RUN pip install --no-cache-dir -r requirements.txt \
    && patchright install-deps chromium \
    && patchright install chromium

COPY . .

CMD ["python", "-u", "bot.py"]
