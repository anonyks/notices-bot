FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
# scrapling's StealthyFetcher drives patchright's own Chromium (not plain
# playwright's), so only patchright needs its browser + system deps installed.
RUN pip install --no-cache-dir -r requirements.txt \
    && patchright install-deps chromium \
    && patchright install chromium

COPY . .

CMD ["python", "-u", "bot.py"]
