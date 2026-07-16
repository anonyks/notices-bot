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
# self-ping so Render free tier doesnt sleep (must hit public URL, not localhost)
health_ping_url = (
    (os.getenv('HEALTH_PING_URL') or '').strip()
    or (os.getenv('RENDER_EXTERNAL_URL') or '').strip()
    or 'https://notices-bot.onrender.com'
)
# Render free spins down ~15 min without inbound HTTP — ping sooner than that
KEEPALIVE_EVERY = int(os.getenv('KEEPALIVE_EVERY', '300'))  # 5 min

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
discord_lock = asyncio.Lock()
discord_cooldown_until = 0.0
discord_direct_blocked_until = 0.0
scrape_error_state = {'exam': '', 'tcioe': ''}


PROXY_HOSTS = [
    'p.webshare.io:80:fexpjpkd-rotate:rx3hdyggy83o',
    'p.webshare.io:80:jtvvlemp-rotate:0fzy5ooc6rn3',
    'p.webshare.io:80:udvdinld-rotate:y3j6holjigwx',
    'p.webshare.io:80:zyqxlgxy-rotate:577vhwv9e3fv',
    'p.webshare.io:80:cgiyhodh-rotate:yjci8dset9za',
    'p.webshare.io:80:kuqjlqsv-6:5k7q3uq6o6j5',
    'p.webshare.io:80:xiggectx-rotate:bv9gz5sk71t3',
    'p.webshare.io:80:lwjfhxdd-rotate:eu1kcrzwlazu',
    'p.webshare.io:80:yllxoyzx-rotate:9nntx8ghhmfn',
    'p.webshare.io:80:dctxwewh-rotate:cb8a3yevk88c',
    'p.webshare.io:80:nckjgccq-rotate:a5gylwfr5l22',
    'p.webshare.io:80:cihjvsyy-rotate:dwcbfsv8dch8',
    'p.webshare.io:80:kuqjlqsv-10:5k7q3uq6o6j5',
]


def get_saved():
    return store.load_posted()


def save(link):
    store.append_posted(link)


async def notify_ops(text):
    """Send a short operator message to all Telegram chats."""
    if not menu:
        return False
    try:
        ok = await menu.send_all(text)
        return bool(ok)
    except Exception as e:
        print(f'[OPS] notify error: {e}')
        return False


async def notify_scrape_error(source, detail):
    detail = (detail or '').strip()[:300]
    if scrape_error_state.get(source) == detail:
        return
    scrape_error_state[source] = detail
    await notify_ops(f'⚠️ Scrape error: {source}\n{detail}')


def clear_scrape_error(source):
    scrape_error_state[source] = ''


# tcioe blocks direct requests; rotate through webshare proxies
def _proxy_url():
    host, port, user, pwd = random.choice(PROXY_HOSTS).split(':')
    return f'http://{user}:{pwd}@{host}:{port}'


def _proxy_urls():
    proxies_list = list(PROXY_HOSTS)
    random.shuffle(proxies_list)
    out = []
    for raw in proxies_list:
        host, port, user, pwd = raw.split(':')
        out.append(f'http://{user}:{pwd}@{host}:{port}')
    return out


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
        clear_scrape_error('exam')
        return notices
    except Exception as e:
        print(f'exam error: {e}')
        await notify_scrape_error('exam', str(e))
        return []


async def get_tcioe():
    url = 'https://cdn.tcioe.edu.np/api/v1/public/notice-mod/notices?limit=10&is_approved_by_campus=true&ordering=-published_at'
    headers = {'User-Agent': UA, 'Accept': 'application/json'}
    try:
        r = None
        last_status = None
        for proxy in _proxy_urls()[:4]:
            try:
                async with httpx.AsyncClient(proxy=proxy, timeout=15) as client:
                    r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    break
                last_status = r.status_code
                if r.status_code == 402:
                    continue
                print(f'tcioe: HTTP {r.status_code}')
                await notify_scrape_error('tcioe', f'HTTP {r.status_code}')
                return []
            except Exception:
                continue
        if r is None or r.status_code != 200:
            if last_status == 402:
                print('tcioe: proxy 402 on all retries')
                await notify_scrape_error('tcioe', 'proxy 402 on all retries')
            elif last_status is not None:
                print(f'tcioe: HTTP {last_status}')
                await notify_scrape_error('tcioe', f'HTTP {last_status}')
            else:
                print('tcioe: request failed on all retries')
                await notify_scrape_error('tcioe', 'request failed on all retries')
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
        clear_scrape_error('tcioe')
        return notices
    except Exception as e:
        print(f'tcioe error: {e}')
        await notify_scrape_error('tcioe', str(e))
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


async def _discord_post(url, *, json=None, data=None, files=None, timeout=30, tries=3):
    """Retry webhook posts with one-at-a-time queue, cooldown, and proxy fallback."""
    global discord_cooldown_until, discord_direct_blocked_until

    def retry_after_seconds(resp):
        retry_after = 2.0
        try:
            retry_after = float((resp.json() or {}).get('retry_after') or retry_after)
        except Exception:
            pass
        header_retry = resp.headers.get('Retry-After')
        if header_retry:
            try:
                retry_after = float(header_retry)
            except Exception:
                pass
        return min(max(retry_after, 1.0), 30.0)

    def is_global_block(resp):
        text = (resp.text or '').lower()
        return 'global rate limits' in text or 'blocked from accessing our api temporarily' in text

    async def post_once(proxy=None):
        if proxy:
            async with httpx.AsyncClient(proxy=proxy, follow_redirects=True, timeout=timeout) as client:
                return await client.post(url, json=json, data=data, files=files)
        return await http.post(url, json=json, data=data, files=files, timeout=timeout)

    async with discord_lock:
        loop = asyncio.get_running_loop()
        last = None
        routes = [None]
        proxy_count = 0

        for attempt in range(1, tries + 1):
            now = loop.time()
            if discord_cooldown_until > now:
                wait = discord_cooldown_until - now
                print(f'discord: cooldown {wait:.1f}s')
                await asyncio.sleep(wait)

            now = loop.time()
            if discord_direct_blocked_until > now:
                if proxy_count == 0:
                    routes = _proxy_urls()[:4]
                    proxy_count = len(routes)
                print(f'discord: direct blocked, using {proxy_count} proxies')
            else:
                routes = [None] + _proxy_urls()[:4]
                proxy_count = len(routes) - 1

            hit_backoff = False
            for proxy in routes:
                last = await post_once(proxy=proxy)
                if last.status_code != 429:
                    return last

                retry_after = retry_after_seconds(last)
                route_name = 'proxy' if proxy else 'direct'

                if is_global_block(last):
                    discord_cooldown_until = max(discord_cooldown_until, loop.time() + retry_after)
                    if proxy is None:
                        # direct route is bad for a while; skip it on later attempts/sends
                        discord_direct_blocked_until = max(
                            discord_direct_blocked_until,
                            loop.time() + max(retry_after * 6, 180.0),
                        )
                        routes = _proxy_urls()[:4]
                        proxy_count = len(routes)
                        print(f'discord: direct route blocked, trying {proxy_count} proxies')
                        continue
                    print(f'discord: global 429 on {route_name}, trying next route')
                    continue

                discord_cooldown_until = max(discord_cooldown_until, loop.time() + retry_after)
                print(f'discord: 429 {route_name} retry in {retry_after}s (try {attempt}/{tries})')
                hit_backoff = True
                break

            if attempt < tries and (hit_backoff or last is not None):
                continue
        return last


async def send_discord(title, url, medias):
    """Send to Discord webhooks. Returns True if at least one post succeeded."""
    posted_any = False
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
                            r = await _discord_post(
                                w,
                                data={'content': msg[:1900]},
                                files={'file': (fn, pr.content)},
                                timeout=30,
                            )
                            if r.status_code in (200, 201, 204):
                                print(f'discord: {fn}')
                                posted_any = True
                                msg = ''
                            else:
                                print(f'discord HTTP {r.status_code}: {r.text[:200]}')
                    except Exception as e:
                        print(f'discord file error: {e}')
            else:
                r = await _discord_post(w, json={'content': msg[:1900]}, timeout=10)
                if r.status_code in (200, 201, 204):
                    print('discord: sent')
                    posted_any = True
                else:
                    print(f'discord HTTP {r.status_code}: {r.text[:200]}')
        except Exception as e:
            print(f'discord error: {e}')
    return posted_any


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
                r = await _discord_post(
                    url,
                    data={'content': caption[:1900]},
                    files={'file': (filename, file_bytes)},
                    timeout=30,
                )
            else:
                r = await _discord_post(url, json={'content': caption[:1900]}, timeout=10)
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
    """Send to Telegram chats. Returns True if at least one send succeeded."""
    posted_any = False
    if not tg_token:
        return False
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
                            posted_any = True
                            local_msg = ''
                        else:
                            print(f'telegram media HTTP {r.status_code}: {r.text[:200]}')
                    except Exception as e:
                        print(f'telegram media error: {e}')
            else:
                r = await http.post(
                    f'https://api.telegram.org/bot{tg_token}/sendMessage',
                    data={'chat_id': chat_id, 'text': msg},
                    timeout=10,
                )
                if r.status_code == 200:
                    print('telegram: sent')
                    posted_any = True
                else:
                    print(f'telegram HTTP {r.status_code}: {r.text[:200]}')
    except Exception as e:
        print(f'telegram error: {e}')
    return posted_any


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
                titles = ', '.join((n.get('title') or 'Untitled') for n in due[:5])
                if len(due) > 5:
                    titles += f' (+{len(due) - 5} more)'
                tg_ok = await menu.send_all(text)
                disc_refs = await send_discord_text_file(text)
                disc_ok = bool(disc_refs)
                if tg_ok or disc_ok:
                    mark_reminder_sent_today()  # only after a successful send attempt
                    print(f'[REMINDER] sent {len(due)} item(s)')
                    await notify_ops(
                        f'⏰ Reminder sent for {len(due)} item(s).\n{titles}\n'
                        f'Telegram: {"ok" if tg_ok else "fail"} | Discord: {"ok" if disc_ok else "fail"}'
                    )
                else:
                    print('[REMINDER] send failed (tg_ok=False disc_ok=False) — not marking sent')
                    await notify_ops(f'⚠️ Reminder failed.\n{titles}')

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
            # expire old assignments quietly
            rows = store.load_manual()
            if dn.mark_expired_rows(rows):
                store.save_manual(rows)

            exam, tcioe = await asyncio.gather(get_exam(), get_tcioe())
            for n in exam + tcioe:
                if n['link'] not in posted:
                    print(f"[MONITOR] NEW: {n['title']}")
                    discord_ok = await send_discord(n['title'], n['link'], n.get('medias'))
                    telegram_ok = await send_telegram(n['title'], n['link'], n.get('medias'))
                    if not (discord_ok or telegram_ok):
                        print('[MONITOR] both sends failed — not saving posted marker')
                        continue
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


async def keepalive_loop():
    """Hit our public URL on a timer so Render free tier stays awake."""
    url = (health_ping_url or '').rstrip('/') + '/'
    if not url or url == '/':
        print('[HEALTH] keepalive off (no HEALTH_PING_URL)')
        return
    print(f'[HEALTH] keepalive every {KEEPALIVE_EVERY}s → {url}')
    # first ping soon after boot (don't wait a full interval)
    await asyncio.sleep(15)
    while True:
        try:
            r = await http.get(url, timeout=10)
            print(f'[HEALTH] ping {r.status_code}')
        except Exception as e:
            print(f'[HEALTH] ping failed: {e}')
        await asyncio.sleep(KEEPALIVE_EVERY)


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
        await asyncio.gather(run(), poll(), reminder_loop(), keepalive_loop())


# health server in background, then run monitor + telegram
Thread(target=run_server, daemon=True).start()

print('=' * 50)
print('NOTICES BOT STARTING...')
print('=' * 50)
store.init_storage()
asyncio.run(main())
