# simple JSON storage for manual + scraped notices
import json
from pathlib import Path
from datetime import datetime, timezone

MANUAL_FILE = Path('manual_notices.json')
SCRAPED_FILE = Path('scraped_notices.json')


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
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')


def load_manual():
    return _load(MANUAL_FILE)


def save_manual(rows):
    _save(MANUAL_FILE, rows)


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
    rows = _load(SCRAPED_FILE)
    rows.append(notice)
    # keep file from growing forever
    if len(rows) > 500:
        rows = rows[-500:]
    _save(SCRAPED_FILE, rows)
