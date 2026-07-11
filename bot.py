import os
import asyncio
import random
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

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
tg_chat = (os.getenv('TELEGRAM_CHAT_ID') or '').strip()
# self-ping so Render free tier doesnt sleep
health_ping_url = (
    (os.getenv('HEALTH_PING_URL') or '').strip()
    or (os.getenv('RENDER_EXTERNAL_URL') or '').strip()
    or 'https://notices-bot.onrender.com'
)

if not tg_token or not tg_chat:
    print('ERROR: TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set!')
    print(f'Token: {tg_token[:20] if tg_token else "EMPTY"}...')
    print(f'Chat: {tg_chat or "EMPTY"}')
    exit(1)

if not w1 and not w2:
    print('WARNING: DISCORD_WEBHOOK1/2 not set — Discord posting disabled')

notify_on = True
offset = 0
http = None


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


async def send_telegram(title, url, medias):
    if not tg_token:
        return
    try:
        msg = f'🎤 {title}\n\n🔗 {url}'
        pdfs = [m['file'] for m in medias if m.get('mediaType') == 'DOCUMENT'] if medias else await get_pdfs(url)

        if pdfs:
            for p in pdfs[:3]:
                try:
                    r = await http.post(
                        f'https://api.telegram.org/bot{tg_token}/sendDocument',
                        data={'chat_id': tg_chat, 'document': p, 'caption': msg},
                        timeout=30,
                    )
                    if r.status_code == 200:
                        print('telegram: sent')
                        msg = ''
                    else:
                        print(f'telegram document HTTP {r.status_code}: {r.text[:200]}')
                except Exception as e:
                    print(f'telegram document error: {e}')
        else:
            await http.post(
                f'https://api.telegram.org/bot{tg_token}/sendMessage',
                data={'chat_id': tg_chat, 'text': msg},
                timeout=10,
            )
            print('telegram: sent')
    except Exception as e:
        print(f'telegram error: {e}')


async def tg_send(txt):
    try:
        await http.post(
            f'https://api.telegram.org/bot{tg_token}/sendMessage',
            data={'chat_id': tg_chat, 'text': txt},
            timeout=10,
        )
        print('[BOT] << Sent reply')
    except Exception as e:
        print(f'[BOT] Send error: {e}')


async def handle_cmd(msg):
    global notify_on
    txt = msg.get('text', '')

    if txt == '/start':
        notify_on = True
        await tg_send(
            'Notifications ON\n\n'
            '/status - Check status\n'
            '/latest - Latest notices\n'
            '/stop - Stop notifications\n'
            '/post - Send custom notice'
        )

    elif txt == '/status':
        status = 'ON' if notify_on else 'OFF'
        posted = get_saved()
        await tg_send(f'Status: {status}\nTotal posted: {len(posted)}')

    elif txt == '/latest':
        exam, tcioe = await asyncio.gather(get_exam(), get_tcioe())
        out = 'LATEST NOTICES:\n\n'
        if exam:
            out += f"EXAM.IOE:\n{exam[0]['title']}\n{exam[0]['link']}\n\n"
        if tcioe:
            out += f"TCIOE:\n{tcioe[0]['title']}\n{tcioe[0]['link']}"
        await tg_send(out if exam or tcioe else 'No notices found')

    elif txt == '/stop':
        notify_on = False
        await tg_send('Notifications stopped\n\nUse /start to resume')

    elif txt.startswith('/post '):
        content = txt[6:].strip()
        if content:
            await send_discord('info_s', content, None)
            await send_telegram('info_s', content, None)
            await tg_send('Posted!')

    # forward telegram docs/photos to discord webhooks
    elif 'document' in msg or 'photo' in msg:
        try:
            if 'document' in msg:
                file_id = msg['document']['file_id']
                caption = msg.get('caption', 'info_s')
            else:
                file_id = msg['photo'][-1]['file_id']  # largest size
                caption = msg.get('caption', 'info_s')

            file_info = (await http.get(
                f'https://api.telegram.org/bot{tg_token}/getFile',
                params={'file_id': file_id},
                timeout=10,
            )).json()
            if file_info.get('ok'):
                file_path = file_info['result']['file_path']
                file_url = f'https://api.telegram.org/file/bot{tg_token}/{file_path}'
                filename = file_path.split('/')[-1]
                fr = await http.get(file_url, timeout=30)
                for w in [w1, w2]:
                    if w:
                        try:
                            await http.post(
                                w,
                                data={'content': f'📄 {caption}'},
                                files={'file': (filename, fr.content)},
                                timeout=30,
                            )
                        except Exception as e:
                            print(f'discord forward error: {e}')
            await tg_send('Posted!')
        except Exception as e:
            print(f'forward error: {e}')
            await tg_send('Failed to post file')


async def poll():
    global offset
    print('[BOT] Starting Telegram bot...')
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
                        await handle_cmd(update['message'])
        except Exception as e:
            print(f'[BOT] Poll error: {e}')
            await asyncio.sleep(2)


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

            if notify_on:
                exam, tcioe = await asyncio.gather(get_exam(), get_tcioe())
                for n in exam + tcioe:
                    if n['link'] not in posted:
                        print(f"[MONITOR] NEW: {n['title']}")
                        await send_discord(n['title'], n['link'], n.get('medias'))
                        await send_telegram(n['title'], n['link'], n.get('medias'))
                        save(n['link'])
                        posted.append(n['link'])
            await asyncio.sleep(CHECK_EVERY)
        except Exception as e:
            print(f'[MONITOR] error: {e}')
            await asyncio.sleep(CHECK_EVERY)


async def main():
    global http
    async with httpx.AsyncClient(follow_redirects=True) as client:
        http = client
        await asyncio.gather(run(), poll())


# health server in background, then run monitor + telegram
Thread(target=run_server, daemon=True).start()

print('=' * 50)
print('NOTICES BOT STARTING...')
print('=' * 50)
asyncio.run(main())
