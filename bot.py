import os
import asyncio
import random
from datetime import datetime, timezone
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

import store
import dates_npt as dn
from tg_bot import (
    TgMenu,
    parse_chat_ids,
    format_reminder_bundle,
    reminder_already_sent_today,
    mark_reminder_sent_today,
)

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36'
# check every 10 min (was 5) so we use less Render bandwidth
CHECK_EVERY = 600


# simple health check server for Render
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')

    def log_message(self, format, *args):
        pass


def run_server():
    port = int(os.getenv('PORT', 10000))
    print(f'[HEALTH] Starting health check server on port {port}...')
    HTTPServer(('0.0.0.0', port), HealthHandler).serve_forever()


# keep files next to this script
script_dir = Path(__file__).parent
os.chdir(script_dir)
load_dotenv()

w1 = (os.getenv('DISCORD_WEBHOOK1') or '').strip()
w2 = (os.getenv('DISCORD_WEBHOOK2') or '').strip()
tg_token = (os.getenv('TELEGRAM_TOKEN') or '').strip()
tg_chat_ids = parse_chat_ids(os.getenv('TELEGRAM_CHAT_ID') or '')
# self-ping so Render free tier doesnt sleep
health_ping_url = (
    (os.getenv('HEALTH_PING_URL') or '').strip()
    or (os.getenv('RENDER_EXTERNAL_URL') or '').strip()
    or 'https://notices-bot.onrender.com'
)

if not tg_token or not tg_chat_ids:
    print('ERROR: TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set!')
    print(f'Token: {tg_token[:20] if tg_token else "EMPTY"}...')
    print(f'Chats: {tg_chat_ids or "EMPTY"}')
    exit(1)

if not w1 and not w2:
    print('WARNING: DISCORD_WEBHOOK1/2 not set — Discord posting disabled')

offset = 0
http = None
menu = None


def get_saved():
    try:
        return Path('posted.txt').read_text().splitlines()
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f'posted.txt read error: {e}')
        return []


def save(link):
    with open('posted.txt', 'a') as f:
        f.write(link + '\n')


# tcioe blocks direct requests; rotate through webshare proxies
def _proxy_url():
    proxies_list = [
        'p.webshare.io:80:xiggectx-rotate:bv9gz5sk71t3',
        'p.webshare.io:80:webhchrs-rotate:gqrtdw324djs',
        'p.webshare.io:80:junpudme-rotate:ghfzxsgqq937',
        'p.webshare.io:80:qehdxkfz-rotate:8l7mty6wkt0j',
        'p.webshare.io:80:yllxoyzx-rotate:9nntx8ghhmfn',
        'p.webshare.io:80:nqahuvka-rotate:9s3475medym6',
        'p.webshare.io:80:rckrqady-rotate:gjx9mtdykoqj',
        'p.webshare.io:80:cukvurbu-rotate:s1kz6oi05y68',
        'p.webshare.io:80:cblgmspz-rotate:idjmw77z4d5l',
        'p.webshare.io:80:lwjfhxdd-rotate:eu1kcrzwlazu',
        'p.webshare.io:80:ahfccqoh-rotate:18ntdktea2cf',
    ]
    host, port, user, pwd = random.choice(proxies_list).split(':')
    return f'http://{user}:{pwd}@{host}:{port}'


async def get_exam():
    try:
        r = await http.get(
            'https://exam.ioe.tu.edu.np/notices',
            headers={'User-Agent': UA},
            timeout=10,
        )
        r.raise_for_status()
        s = BeautifulSoup(r.content, 'html.parser')
        notices = []
        seen = set()
        for a in s.find_all('a', href=lambda x: x and 'notices/' in str(x)):
            h = a.get('href')
            t = a.get_text().strip()
            if h and t and h not in seen:
                if not h.startswith('http'):
                    h = 'https://exam.ioe.tu.edu.np' + h
                notices.append({'link': h, 'title': t, 'medias': None})
                seen.add(h)
        print(f'exam: {len(notices)}')
        return notices
    except Exception as e:
        print(f'exam error: {e}')
        return []


async def get_tcioe():
    try:
        proxy = _proxy_url()
        async with httpx.AsyncClient(proxy=proxy, timeout=15) as client:
            r = await client.get(
                'https://cdn.tcioe.edu.np/api/v1/public/notice-mod/notices?limit=10&is_approved_by_campus=true&ordering=-published_at',
                headers={'User-Agent': UA, 'Accept': 'application/json'},
            )
        if r.status_code != 200:
            print(f'tcioe: HTTP {r.status_code}')
            return []

        d = r.json()
        notices = []
        for i in d.get('results', []):
            notices.append({
                'link': f"https://tcioe.edu.np/notices/{i.get('slug', i.get('uuid'))}",
                'title': i.get('title', ''),
                'medias': i.get('medias', []),
            })
        print(f'tcioe: {len(notices)}')
        return notices
    except Exception as e:
        print(f'tcioe error: {e}')
        return []


async def get_pdfs(url):
    try:
        r = await http.get(url, headers={'User-Agent': UA}, timeout=10)
        r.raise_for_status()
        s = BeautifulSoup(r.content, 'html.parser')
        pdfs = []
        for a in s.find_all('a', href=lambda x: x and ('.pdf' in str(x).lower() or '/medias/' in str(x))):
            h = a.get('href')
            if h:
                if not h.startswith('http'):
                    h = 'https://portal.tu.edu.np' + h if h.startswith('/') else 'https://exam.ioe.tu.edu.np/' + h
                if h not in pdfs:
                    pdfs.append(h)
        return pdfs
    except Exception as e:
        print(f'get_pdfs error ({url}): {e}')
        return []


async def send_discord(title, url, medias):
    for w in [w1, w2]:
        if not w:
            continue
        try:
            msg = f'{title}\n\n{url}'
            pdfs = [m['file'] for m in medias if m.get('mediaType') == 'DOCUMENT'] if medias else await get_pdfs(url)

            if pdfs:
                for p in pdfs[:10]:
                    try:
                        pr = await http.get(p, headers={'User-Agent': UA}, timeout=30)
                        if pr.status_code == 200:
                            fn = p.split('/')[-1]  # keep original filename/extension
                            if not fn or '?' in fn:
                                fn = 'file.pdf'
                            await http.post(w, data={'content': msg}, files={'file': (fn, pr.content)}, timeout=30)
                            print(f'discord: {fn}')
                            msg = ''
                    except Exception as e:
                        print(f'discord file error: {e}')
            else:
                await http.post(w, json={'content': msg}, timeout=10)
                print('discord: sent')
        except Exception as e:
            print(f'discord error: {e}')


async def send_discord_text_file(caption, file_bytes=None, filename=None):
    """Used by manual menu / quick post."""
    for w in [w1, w2]:
        if not w:
            continue
        try:
            if file_bytes and filename:
                await http.post(
                    w,
                    data={'content': caption[:1900]},
                    files={'file': (filename, file_bytes)},
                    timeout=30,
                )
            else:
                await http.post(w, json={'content': caption[:1900]}, timeout=10)
            print('discord: manual/quick sent')
        except Exception as e:
            print(f'discord manual error: {e}')


async def send_telegram(title, url, medias):
    if not tg_token:
        return
    try:
        msg = f'🌐 from_site\n🎤 {title}\n\n🔗 {url}'
        pdfs = [m['file'] for m in medias if m.get('mediaType') == 'DOCUMENT'] if medias else await get_pdfs(url)

        for chat_id in tg_chat_ids:
            if pdfs:
                local_msg = msg
                for p in pdfs[:3]:
                    try:
                        r = await http.post(
                            f'https://api.telegram.org/bot{tg_token}/sendDocument',
                            data={'chat_id': chat_id, 'document': p, 'caption': local_msg},
                            timeout=30,
                        )
                        if r.status_code == 200:
                            print('telegram: sent')
                            local_msg = ''
                        else:
                            print(f'telegram document HTTP {r.status_code}: {r.text[:200]}')
                    except Exception as e:
                        print(f'telegram document error: {e}')
            else:
                await http.post(
                    f'https://api.telegram.org/bot{tg_token}/sendMessage',
                    data={'chat_id': chat_id, 'text': msg},
                    timeout=10,
                )
                print('telegram: sent')
    except Exception as e:
        print(f'telegram error: {e}')


async def poll():
    global offset
    print('[BOT] Starting Telegram bot...')
    await menu.setup_commands()

    # skip backlog so we don't re-handle old commands on restart
    try:
        r = await http.get(f'https://api.telegram.org/bot{tg_token}/getUpdates', timeout=5)
        data = r.json()
        if data.get('ok'):
            if data.get('result'):
                offset = data['result'][-1]['update_id'] + 1
                print(f'[BOT] Cleared {len(data["result"])} old msgs')
            else:
                print('[BOT] No old messages')
        else:
            print(f'[BOT] API error: {data.get("description", "unknown")}')
    except Exception as e:
        print(f'[BOT] Clear error: {e}')

    print('[BOT] Ready - listening for commands...')
    while True:
        try:
            r = await http.get(
                f'https://api.telegram.org/bot{tg_token}/getUpdates',
                params={'offset': offset, 'timeout': 25},
                timeout=35,
            )
            data = r.json()
            if data.get('ok'):
                for update in data.get('result', []):
                    offset = update['update_id'] + 1
                    if 'message' in update:
                        txt = update['message'].get('text', '')
                        print(f'[BOT] >> {txt}')
                    elif 'callback_query' in update:
                        print(f"[BOT] >> cb {update['callback_query'].get('data')}")
                    await menu.handle_update(update)
        except Exception as e:
            print(f'[BOT] Poll error: {e}')
            await asyncio.sleep(2)


async def reminder_loop():
    """At 6:00 PM Nepal time, remind about assignments due tomorrow. No catch-up if missed."""
    print('[REMINDER] Deadline reminder loop started (6:00 PM NPT)')
    while True:
        try:
            wait_s = dn.seconds_until_next_6pm_npt()
            print(f'[REMINDER] sleeping {wait_s}s until next 6pm NPT')
            await asyncio.sleep(wait_s)

            now = dn.now_npt()
            # only fire near 6pm; if we missed the window, skip (no catch-up)
            if now.hour != 18:
                print(f'[REMINDER] skip — not 6pm window (hour={now.hour})')
                await asyncio.sleep(30)
                continue

            if reminder_already_sent_today():
                print('[REMINDER] already sent today — skip')
                await asyncio.sleep(70)
                continue

            rows = store.load_manual()
            if dn.mark_expired_rows(rows):
                store.save_manual(rows)
            due = dn.deadlines_tomorrow(rows, now=now)
            mark_reminder_sent_today()  # mark even if empty, so restart won't re-spam later
            if not due:
                print('[REMINDER] nothing due tomorrow')
            else:
                text = format_reminder_bundle(due)
                await menu.send_all(text)
                print(f'[REMINDER] sent {len(due)} item(s)')

            await asyncio.sleep(70)
        except Exception as e:
            print(f'[REMINDER] error: {e}')
            await asyncio.sleep(60)


async def run():
    print('[MONITOR] Starting notice monitor...')
    posted = get_saved()

    # first run: mark current notices posted so we don't spam old ones
    if not posted:
        try:
            print('[MONITOR] First run, saving existing...')
            exam, tcioe = await asyncio.gather(get_exam(), get_tcioe())
            for n in exam + tcioe:
                save(n['link'])
            posted = get_saved()
            print(f'[MONITOR] Saved {len(posted)} - wont post old notices')
            await asyncio.sleep(5)
        except Exception as e:
            print(f'[MONITOR] First run error: {e}')
            await asyncio.sleep(5)

    print('[MONITOR] Monitoring every 10 min...')
    while True:
        try:
            # self-ping to keep render awake
            if health_ping_url:
                try:
                    await http.get(health_ping_url.rstrip('/') + '/', timeout=5)
                except Exception as e:
                    print(f'[HEALTH] ping failed: {e}')

            # expire old assignments quietly
            rows = store.load_manual()
            if dn.mark_expired_rows(rows):
                store.save_manual(rows)

            exam, tcioe = await asyncio.gather(get_exam(), get_tcioe())
            for n in exam + tcioe:
                if n['link'] not in posted:
                    print(f"[MONITOR] NEW: {n['title']}")
                    await send_discord(n['title'], n['link'], n.get('medias'))
                    await send_telegram(n['title'], n['link'], n.get('medias'))
                    store.append_scraped({
                        'category': 'from_site',
                        'title': n['title'],
                        'link': n['link'],
                        'posted_at': datetime.now(timezone.utc).isoformat(),
                    })
                    save(n['link'])
                    posted.append(n['link'])
            await asyncio.sleep(CHECK_EVERY)
        except Exception as e:
            print(f'[MONITOR] error: {e}')
            await asyncio.sleep(CHECK_EVERY)


async def main():
    global http, menu
    async with httpx.AsyncClient(follow_redirects=True) as client:
        http = client
        menu = TgMenu(
            http=http,
            token=tg_token,
            chat_ids=tg_chat_ids,
            discord_webhooks=[w1, w2],
            send_discord_text_file=send_discord_text_file,
        )
        await asyncio.gather(run(), poll(), reminder_loop())


# health server in background, then run monitor + telegram
Thread(target=run_server, daemon=True).start()

print('=' * 50)
print('NOTICES BOT STARTING...')
print('=' * 50)
asyncio.run(main())
