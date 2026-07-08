import os, requests, asyncio
from bs4 import BeautifulSoup
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

# simple health check server
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, format, *args):
        pass

def run_server():
    port = int(os.getenv('PORT', 10000))
    HTTPServer(('0.0.0.0', port), HealthHandler).serve_forever()

# change to script directory
script_dir = Path(__file__).parent
os.chdir(script_dir)

# load .env
try:
    with open('.env', 'r') as f:
        for line in f:
            line = line.strip()
            if line and '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ[k.strip()] = v.strip()
except Exception as e:
    print(f'Warning: Could not load .env: {e}')

# tokens
w1 = os.getenv('DISCORD_WEBHOOK1', '')
w2 = os.getenv('DISCORD_WEBHOOK2', '')
tg_token = os.getenv('TELEGRAM_TOKEN', '')
tg_chat = os.getenv('TELEGRAM_CHAT_ID', '')

if not tg_token or not tg_chat:
    print('ERROR: TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set!')
    print(f'Token: {tg_token[:20] if tg_token else "EMPTY"}...')
    print(f'Chat: {tg_chat}')
    exit(1)

# state
notify_on = True
offset = 0

# get saved links
def get_saved():
    try:
        return open('posted.txt').read().splitlines()
    except:
        return []

# save link
def save(link):
    open('posted.txt', 'a').write(link + '\n')

# scrape exam.ioe
def get_exam():
    try:
        r = requests.get('https://exam.ioe.tu.edu.np/notices', headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
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
    except:
        return []

# scrape tcioe api
def get_tcioe():
    try:
        r = requests.get('https://cdn.tcioe.edu.np/api/v1/public/notice-mod/notices?limit=10&is_approved_by_campus=true&ordering=-published_at', timeout=10)
        d = r.json()
        notices = []
        for i in d.get('results', []):
            notices.append({
                'link': f"https://tcioe.edu.np/notices/{i.get('slug', i.get('uuid'))}",
                'title': i.get('title', ''),
                'medias': i.get('medias', [])
            })
        print(f'tcioe: {len(notices)}')
        return notices
    except:
        return []

# get pdfs from page
def get_pdfs(url):
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
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
    except:
        return []

# send to discord
async def send_discord(title, url, medias):
    for w in [w1, w2]:
        if not w:
            continue
        try:
            msg = f"{title}\n\n{url}"
            pdfs = [m['file'] for m in medias if m.get('mediaType') == 'DOCUMENT'] if medias else get_pdfs(url)
            
            if pdfs:
                for p in pdfs[:10]:
                    try:
                        pr = requests.get(p, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
                        if pr.status_code == 200:
                            fn = p.split('/')[-1] if p.split('/')[-1].endswith('.pdf') else 'file.pdf'
                            requests.post(w, data={'content': msg}, files={'file': (fn, pr.content)}, timeout=30)
                            print(f'discord: {fn}')
                            msg = ''
                    except:
                        pass
            else:
                requests.post(w, json={'content': msg}, timeout=10)
                print('discord: sent')
        except:
            pass

# send to telegram
async def send_telegram(title, url, medias):
    if not tg_token:
        return
    try:
        msg = f"🎤 {title}\n\n🔗 {url}"
        pdfs = [m['file'] for m in medias if m.get('mediaType') == 'DOCUMENT'] if medias else get_pdfs(url)
        
        if pdfs:
            for p in pdfs[:3]:
                try:
                    r = requests.post(f"https://api.telegram.org/bot{tg_token}/sendDocument",
                                    data={'chat_id': tg_chat, 'document': p, 'caption': msg}, timeout=30)
                    if r.status_code == 200:
                        print('telegram: sent')
                        msg = ''
                except:
                    pass
        else:
            requests.post(f"https://api.telegram.org/bot{tg_token}/sendMessage",
                        data={'chat_id': tg_chat, 'text': msg})
            print('telegram: sent')
    except:
        pass

# send to messenger
# send msg to telegram
def tg_send(txt):
    try:
        r = requests.post(f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    data={'chat_id': tg_chat, 'text': txt})
        print(f'[BOT] << Sent reply')
    except Exception as e:
        print(f'[BOT] Send error: {e}')

# send file to telegram
def tg_file(file_url, caption=''):
    try:
        requests.post(f"https://api.telegram.org/bot{tg_token}/sendDocument",
                    data={'chat_id': tg_chat, 'document': file_url, 'caption': caption}, timeout=30)
    except:
        pass

# send photo to telegram
def tg_photo(photo_url, caption=''):
    try:
        requests.post(f"https://api.telegram.org/bot{tg_token}/sendPhoto",
                    data={'chat_id': tg_chat, 'photo': photo_url, 'caption': caption}, timeout=30)
    except:
        pass

# handle commands
async def handle_cmd(msg):
    global notify_on
    txt = msg.get('text', '')
    
    if txt == '/start':
        tg_send('Notices Bot\n\n/status - Check status\n/latest - Latest notices\n/stop - Stop notifications\n/post - Send custom notice')
    
    elif txt == '/status':
        status = 'ON' if notify_on else 'OFF'
        posted = get_saved()
        tg_send(f'Status: {status}\nTotal posted: {len(posted)}')
    
    elif txt == '/latest':
        exam = get_exam()
        tcioe = get_tcioe()
        msg = 'LATEST NOTICES:\n\n'
        if exam:
            msg += f"EXAM.IOE:\n{exam[0]['title']}\n{exam[0]['link']}\n\n"
        if tcioe:
            msg += f"TCIOE:\n{tcioe[0]['title']}\n{tcioe[0]['link']}"
        tg_send(msg if exam or tcioe else 'No notices found')
    
    elif txt == '/stop':
        notify_on = False
        tg_send('Notifications stopped\n\nUse /start to resume')
    
    elif txt.startswith('/post '):
        content = txt[6:].strip()
        if content:
            await send_discord("info_s", content, None)
            await send_telegram("info_s", content, None)
            tg_send('Posted!')
    
    # handle files/photos with /post
    elif 'document' in msg or 'photo' in msg:
        if 'document' in msg:
            file_id = msg['document']['file_id']
            caption = msg.get('caption', 'info_s')
            # forward to discord
            file_info = requests.get(f"https://api.telegram.org/bot{tg_token}/getFile?file_id={file_id}").json()
            if file_info.get('ok'):
                file_path = file_info['result']['file_path']
                file_url = f"https://api.telegram.org/file/bot{tg_token}/{file_path}"
                for w in [w1, w2]:
                    if w:
                        try:
                            fr = requests.get(file_url, timeout=30)
                            requests.post(w, data={'content': f"📄 {caption}"}, files={'file': ('file.pdf', fr.content)}, timeout=30)
                        except:
                            pass
        elif 'photo' in msg:
            photo = msg['photo'][-1]  # largest
            file_id = photo['file_id']
            caption = msg.get('caption', 'info_s')
            file_info = requests.get(f"https://api.telegram.org/bot{tg_token}/getFile?file_id={file_id}").json()
            if file_info.get('ok'):
                file_path = file_info['result']['file_path']
                file_url = f"https://api.telegram.org/file/bot{tg_token}/{file_path}"
                for w in [w1, w2]:
                    if w:
                        try:
                            fr = requests.get(file_url, timeout=30)
                            requests.post(w, data={'content': f"📄 {caption}"}, files={'file': ('image.jpg', fr.content)}, timeout=30)
                        except:
                            pass
        tg_send('Posted!')

# poll telegram
async def poll():
    global offset
    print('[BOT] Starting Telegram bot...')
    # skip old messages
    try:
        r = requests.get(f"https://api.telegram.org/bot{tg_token}/getUpdates", timeout=5)
        data = r.json()
        if data.get('ok') and data.get('result'):
            offset = data['result'][-1]['update_id'] + 1
            print(f'[BOT] Cleared {len(data["result"])} old msgs')
        else:
            print(f'[BOT] API error: {data.get("description", "unknown")}')
    except Exception as e:
        print(f'[BOT] Clear error: {e}')
    
    print('[BOT] Ready - listening for commands...')
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{tg_token}/getUpdates?offset={offset}&timeout=5", timeout=10)
            data = r.json()
            if data.get('ok'):
                for update in data.get('result', []):
                    offset = update['update_id'] + 1
                    if 'message' in update:
                        txt = update['message'].get('text', '')
                        print(f"[BOT] >> {txt}")
                        await handle_cmd(update['message'])
        except Exception as e:
            print(f'[BOT] Poll error: {e}')
        await asyncio.sleep(1)

# main checker
async def run():
    global notify_on
    print('[MONITOR] Starting notice monitor...')
    posted = get_saved()
    
    # first run
    if not posted:
        print('[MONITOR] First run, saving existing...')
        for n in get_exam() + get_tcioe():
            save(n['link'])
        posted = get_saved()
        print(f'[MONITOR] Saved {len(posted)} - wont post old notices')
        await asyncio.sleep(5)  # short wait
    
    print('[MONITOR] Monitoring every 5 min...')
    # loop
    while True:
        try:
            if notify_on:
                for n in get_exam() + get_tcioe():
                    if n['link'] not in posted:
                        print(f"[MONITOR] NEW: {n['title']}")
                        await send_discord(n['title'], n['link'], n.get('medias'))
                        await send_telegram(n['title'], n['link'], n.get('medias'))
                        save(n['link'])
                        posted.append(n['link'])
            await asyncio.sleep(300)
        except Exception as e:
            print(f'[MONITOR] error: {e}')
            await asyncio.sleep(300)

# run both
async def main():
    await asyncio.gather(run(), poll())

# start health check server
Thread(target=run_server, daemon=True).start()

print('='*50)
print('NOTICES BOT STARTING...')
print('='*50)
asyncio.run(main())
