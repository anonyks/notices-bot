# simple JSON storage for manual + scraped notices
# On Render free, local files vanish on deploy — optional GitHub Gist keeps them.
import json
import os
import threading
import time
from pathlib import Path
from datetime import datetime, timezone

try:
    import httpx
except ImportError:
    httpx = None


def data_dir():
    d = Path(os.getenv('DATA_DIR') or '.').expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def manual_path():
    return data_dir() / 'manual_notices.json'


def scraped_path():
    return data_dir() / 'scraped_notices.json'


def posted_path():
    return data_dir() / 'posted.txt'


def reminder_path():
    return data_dir() / 'reminder_sent_day.txt'


def _load(path):
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f'[STORE] read error {path}: {e}')
        return []


def _save(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    schedule_gist_backup()


def load_manual():
    return _load(manual_path())


def save_manual(rows):
    _save(manual_path(), rows)


def next_manual_id(rows=None):
    rows = rows if rows is not None else load_manual()
    nums = []
    for r in rows:
        try:
            nums.append(int(str(r.get('id', '0')).lstrip('m')))
        except ValueError:
            pass
    return f"m{(max(nums) + 1) if nums else 1}"


def add_manual(notice):
    rows = load_manual()
    notice['id'] = notice.get('id') or next_manual_id(rows)
    notice['created_at'] = notice.get('created_at') or datetime.now(timezone.utc).isoformat()
    rows.append(notice)
    save_manual(rows)
    return notice


def get_manual(notice_id):
    for r in load_manual():
        if r.get('id') == notice_id:
            return r
    return None


def update_manual(notice_id, **fields):
    rows = load_manual()
    for r in rows:
        if r.get('id') == notice_id:
            r.update(fields)
            r['updated_at'] = datetime.now(timezone.utc).isoformat()
            save_manual(rows)
            return r
    return None


def delete_manual(notice_id):
    rows = load_manual()
    new_rows = [r for r in rows if r.get('id') != notice_id]
    if len(new_rows) == len(rows):
        return False
    save_manual(new_rows)
    return True


def recent_manual(limit=10):
    rows = load_manual()
    rows = sorted(rows, key=lambda r: r.get('created_at', ''), reverse=True)
    return rows[:limit]


def append_scraped(notice):
    path = scraped_path()
    rows = _load(path)
    rows.append(notice)
    if len(rows) > 500:
        rows = rows[-500:]
    _save(path, rows)


def load_posted():
    try:
        return posted_path().read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f'[STORE] posted read error: {e}')
        return []


def append_posted(link):
    path = posted_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(link + '\n')
    schedule_gist_backup()


def replace_posted(links):
    """Write posted list once (first-run seed) — one gist backup."""
    path = posted_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = '\n'.join(links) + ('\n' if links else '')
    path.write_text(text, encoding='utf-8')
    schedule_gist_backup()


def reminder_day():
    try:
        return reminder_path().read_text(encoding='utf-8').strip()
    except Exception:
        return ''


def set_reminder_day(day):
    path = reminder_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(day).strip() + '\n', encoding='utf-8')
    schedule_gist_backup()


def _snapshot():
    return {
        'manual': load_manual(),
        'scraped': _load(scraped_path()),
        'posted': load_posted(),
        'reminder_day': reminder_day(),
    }


def _apply_snapshot(data):
    if not isinstance(data, dict):
        return False
    _save_local_only(manual_path(), data.get('manual') or [])
    _save_local_only(scraped_path(), data.get('scraped') or [])
    posted = data.get('posted') or []
    posted_path().write_text('\n'.join(posted) + ('\n' if posted else ''), encoding='utf-8')
    rem = (data.get('reminder_day') or '').strip()
    if rem:
        reminder_path().write_text(rem + '\n', encoding='utf-8')
    elif reminder_path().exists():
        reminder_path().write_text('', encoding='utf-8')
    return True


def _save_local_only(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')


def _local_has_data():
    if load_manual() or load_posted() or _load(scraped_path()):
        return True
    return False


def _migrate_cwd_files():
    """If DATA_DIR is set, copy old /app root files once."""
    d = data_dir()
    cwd = Path('.').resolve()
    if d == cwd:
        return
    for name in (
        'manual_notices.json',
        'scraped_notices.json',
        'posted.txt',
        'reminder_sent_day.txt',
    ):
        src = cwd / name
        dst = d / name
        if src.exists() and not dst.exists():
            dst.write_bytes(src.read_bytes())
            print(f'[STORE] migrated {name} → {d}')


def _gist_config():
    token = (os.getenv('GITHUB_TOKEN') or '').strip()
    gist_id = (os.getenv('STATE_GIST_ID') or '').strip()
    return token, gist_id


def _pull_gist():
    token, gist_id = _gist_config()
    if not token or not gist_id or httpx is None:
        return False
    try:
        r = httpx.get(
            f'https://api.github.com/gists/{gist_id}',
            headers={
                'Authorization': f'Bearer {token}',
                'Accept': 'application/vnd.github+json',
            },
            timeout=20,
        )
        if r.status_code != 200:
            print(f'[STORE] gist pull HTTP {r.status_code}: {r.text[:200]}')
            return False
        files = r.json().get('files') or {}
        raw = None
        for name in ('bot_state.json', 'state.json'):
            if name in files and files[name].get('content') is not None:
                raw = files[name]['content']
                break
        if raw is None:
            for meta in files.values():
                if meta.get('content') is not None:
                    raw = meta['content']
                    break
        if not raw:
            print('[STORE] gist is empty')
            return False
        data = json.loads(raw)
        ok = _apply_snapshot(data)
        if ok:
            print('[STORE] restored state from GitHub Gist')
        return ok
    except Exception as e:
        print(f'[STORE] gist pull error: {e}')
        return False


_pushing = False
_push_lock = threading.Lock()
_push_timer = None
_PUSH_DELAY_SEC = 3.0


def schedule_gist_backup():
    """Coalesce many rapid writes into one Gist PATCH (avoids GitHub 409)."""
    global _push_timer
    token, gist_id = _gist_config()
    if not token or not gist_id or httpx is None:
        return
    with _push_lock:
        if _push_timer is not None:
            _push_timer.cancel()
        _push_timer = threading.Timer(_PUSH_DELAY_SEC, _push_gist_now)
        _push_timer.daemon = True
        _push_timer.start()


def flush_gist_backup():
    """Push immediately (e.g. end of first-run seed)."""
    global _push_timer
    with _push_lock:
        if _push_timer is not None:
            _push_timer.cancel()
            _push_timer = None
    _push_gist_now()


def _push_gist_now():
    global _pushing
    token, gist_id = _gist_config()
    if not token or not gist_id or httpx is None:
        return
    with _push_lock:
        if _pushing:
            t = threading.Timer(2.0, _push_gist_now)
            t.daemon = True
            t.start()
            return
        _pushing = True
    try:
        body = {
            'files': {
                'bot_state.json': {
                    'content': json.dumps(_snapshot(), ensure_ascii=False, indent=2),
                }
            }
        }
        for attempt in range(3):
            r = httpx.patch(
                f'https://api.github.com/gists/{gist_id}',
                headers={
                    'Authorization': f'Bearer {token}',
                    'Accept': 'application/vnd.github+json',
                },
                json=body,
                timeout=20,
            )
            if r.status_code in (200, 201):
                print('[STORE] gist backup ok')
                return
            if r.status_code == 409 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            print(f'[STORE] gist push HTTP {r.status_code}: {r.text[:200]}')
            return
    except Exception as e:
        print(f'[STORE] gist push error: {e}')
    finally:
        with _push_lock:
            _pushing = False


def init_storage():
    d = data_dir()
    print(f'[STORE] DATA_DIR={d}')
    _migrate_cwd_files()
    if not _local_has_data():
        _pull_gist()
    token, gist_id = _gist_config()
    on_render = bool(os.getenv('RENDER') or os.getenv('RENDER_EXTERNAL_URL'))
    if on_render and not gist_id:
        print(
            '[STORE] WARNING: STATE_GIST_ID not set — '
            'manual notices / posted.txt reset on every Render deploy. '
            'Create a secret Gist, add GITHUB_TOKEN + STATE_GIST_ID (see DEPLOY.md).'
        )
    elif gist_id and token:
        print(f'[STORE] Gist backup enabled ({gist_id[:8]}…)')
    elif gist_id and not token:
        print('[STORE] WARNING: STATE_GIST_ID set but GITHUB_TOKEN missing')
