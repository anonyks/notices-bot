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
    format_from_site,
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
    return store.load_posted()


def save(link):
    store.append_posted(link)


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


async def get_notice_attachments(url):
    """PDFs + images from the notice body only (not page chrome / other notices)."""
    try:
        r = await http.get(url, headers={'User-Agent': UA}, timeout=10)
        r.raise_for_status()
        s = BeautifulSoup(r.content, 'html.parser')
        # exam.ioe detail body; never scan whole page (logos, sidebar, highlights)
        root = (
            s.select_one('.detail-page-inner')
            or s.select_one('.detail-page .ck-table')
            or s.select_one('article')
        )
        if not root:
            print(f'get_notice_attachments: no detail body on {url}')
            return []

        out = []
        seen = set()

        def norm_key(h):
            # treat http/https as the same file
            low = h.lower().split('?')[0].split('#')[0]
            if low.startswith('http://'):
                low = 'https://' + low[len('http://'):]
            return low

        def add(href, kind):
            if not href or href.startswith('data:'):
                return
            h = href.strip()
            if not h.startswith('http'):
                if h.startswith('/'):
                    h = 'https://portal.tu.edu.np' + h if '/medias/' in h else 'https://exam.ioe.tu.edu.np' + h
                else:
                    h = 'https://exam.ioe.tu.edu.np/' + h
            # prefer https when both exist
            if h.startswith('http://'):
                h = 'https://' + h[len('http://'):]
            key = norm_key(h)
            if key in seen:
                return
            seen.add(key)
            out.append({'url': h, 'kind': kind})

        for a in root.find_all('a', href=True):
            h = a['href']
            low = h.lower()
            if '.pdf' in low or '/medias/' in low:
                kind = 'image' if any(low.split('?')[0].endswith(ext) for ext in (
                    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'
                )) else 'document'
                add(h, kind)

        for img in root.find_all('img', src=True):
            src = img['src']
            low = src.lower()
            # skip obvious chrome
            if any(x in low for x in ('logo', 'icon', 'avatar', 'sprite', 'favicon')):
                continue
            add(src, 'image')

        return prefer_pdf_over_images(out)
    except Exception as e:
        print(f'get_notice_attachments error ({url}): {e}')
        return []


def prefer_pdf_over_images(files):
    """If any PDF/document is present, skip images (notice scan of a PDF, etc.)."""
    docs = [f for f in files if f.get('kind') != 'image']
    if docs:
        return docs
    return files


def _attachments_from_medias(medias):
    """Normalize tcioe-style medias list."""
    out = []
    for m in medias or []:
        f = m.get('file')
        if not f:
            continue
        mt = (m.get('mediaType') or '').upper()
        if mt in ('IMAGE', 'PHOTO', 'IMG'):
            out.append({'url': f, 'kind': 'image'})
        else:
            out.append({'url': f, 'kind': 'document'})
    return prefer_pdf_over_images(out)


async def send_discord(title, url, medias):
    for w in [w1, w2]:
        if not w:
            continue
        try:
            msg = format_from_site(title, url)
            files = _attachments_from_medias(medias) if medias else await get_notice_attachments(url)

            if files:
                for item in files[:10]:
                    try:
                        pr = await http.get(item['url'], headers={'User-Agent': UA}, timeout=30)
                        if pr.status_code == 200:
                            fn = item['url'].split('/')[-1].split('?')[0] or (
                                'image.png' if item['kind'] == 'image' else 'file.pdf'
                            )
                            await http.post(
                                w,
                                data={'content': msg[:1900]},
                                files={'file': (fn, pr.content)},
                                timeout=30,
                            )
                            print(f'discord: {fn}')
                            msg = ''
                    except Exception as e:
                        print(f'discord file error: {e}')
            else:
                await http.post(w, json={'content': msg[:1900]}, timeout=10)
                print('discord: sent')
        except Exception as e:
            print(f'discord error: {e}')


async def send_discord_text_file(caption, file_bytes=None, filename=None):
    """Post to Discord webhooks. Returns [{webhook_url, message_id}, ...] when possible."""
    refs = []
    for w in [w1, w2]:
        if not w:
            continue
        try:
            # wait=true so Discord returns the message id (needed for edit/delete)
            url = w if 'wait=' in w else (w + ('&' if '?' in w else '?') + 'wait=true')
            if file_bytes and filename:
                r = await http.post(
                    url,
                    data={'content': caption[:1900]},
                    files={'file': (filename, file_bytes)},
                    timeout=30,
                )
            else:
                r = await http.post(url, json={'content': caption[:1900]}, timeout=10)
            if r.status_code in (200, 201):
                try:
                    mid = r.json().get('id')
                    if mid:
                        # store base webhook url without wait query
                        base = w.split('?')[0]
                        refs.append({'webhook_url': base, 'message_id': str(mid)})
                except Exception:
                    pass
                print('discord: manual/quick sent')
            else:
                print(f'discord manual HTTP {r.status_code}: {r.text[:200]}')
        except Exception as e:
            print(f'discord manual error: {e}')
    return refs


async def edit_discord_message(webhook_url, message_id, content):
    try:
        base = webhook_url.split('?')[0]
        r = await http.patch(
            f'{base}/messages/{message_id}',
            json={'content': content[:2000]},
            timeout=15,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        print(f'discord edit error: {e}')
        return False


async def delete_discord_message(webhook_url, message_id):
    try:
        base = webhook_url.split('?')[0]
        r = await http.delete(f'{base}/messages/{message_id}', timeout=15)
        return r.status_code in (200, 204)
    except Exception as e:
        print(f'discord delete error: {e}')
        return False


async def send_telegram(title, url, medias):
    if not tg_token:
        return
    try:
        msg = format_from_site(title, url)
        files = _attachments_from_medias(medias) if medias else await get_notice_attachments(url)

        for chat_id in tg_chat_ids:
            if files:
                local_msg = msg
                for item in files[:3]:
                    try:
                        if item['kind'] == 'image':
                            r = await http.post(
                                f'https://api.telegram.org/bot{tg_token}/sendPhoto',
                                data={'chat_id': chat_id, 'photo': item['url'], 'caption': local_msg},
                                timeout=30,
                            )
                        else:
                            r = await http.post(
                                f'https://api.telegram.org/bot{tg_token}/sendDocument',
                                data={'chat_id': chat_id, 'document': item['url'], 'caption': local_msg},
                                timeout=30,
                            )
                        if r.status_code == 200:
                            print('telegram: sent')
                            local_msg = ''
                        else:
                            print(f'telegram media HTTP {r.status_code}: {r.text[:200]}')
                    except Exception as e:
                        print(f'telegram media error: {e}')
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
            if not due:
                mark_reminder_sent_today()  # empty day — don't re-check all night
                print('[REMINDER] nothing due tomorrow')
            else:
                text = format_reminder_bundle(due)
                await menu.send_all(text)
                await send_discord_text_file(text)
                mark_reminder_sent_today()  # only after a successful send attempt
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
            links = [n['link'] for n in exam + tcioe]
            store.replace_posted(links)
            store.flush_gist_backup()
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
            edit_discord_message=edit_discord_message,
            delete_discord_message=delete_discord_message,
        )
        await asyncio.gather(run(), poll(), reminder_loop())


# health server in background, then run monitor + telegram
Thread(target=run_server, daemon=True).start()

print('=' * 50)
print('NOTICES BOT STARTING...')
print('=' * 50)
store.init_storage()
asyncio.run(main())
