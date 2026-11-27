# BCT 2081-AB sem5 class routine change tracker.
# First run only establishes a baseline (sends nothing); later runs notify
# (Telegram + Discord, like any other notice) only when the routine content
# actually changed. State persists via store.py so it survives Render redeploys.
import asyncio
import hashlib
import html as html_lib
import json
from datetime import datetime, timedelta, timezone

import httpx

from weasyprint import HTML

import store

ROUTINE_URL = 'https://routine.tcioe.edu.np/api/get-routine'
ROUTINE_PARAMS = {'faculty': 'BCT', 'batch_year': '2081', 'section': 'AB', 'semester': 'sem5'}
# Cloudflare blocks Render's IP on a bare request (same as tcioe's old
# cdn block) -- the full browser-style header set gets through.
ROUTINE_HEADERS = {
    'x-routine-pass': 'tcioe-routne-929282383838383',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.7',
    'Cache-Control': 'max-age=0',
    'Upgrade-Insecure-Requests': '1',
}

DAY_ORDER = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
PERIODS = [
    ('I', '09:15 - 10:00'), ('II', '10:00 - 10:45'), ('III', '10:45 - 11:30'),
    ('IV', '11:30 - 12:15'), ('V', '12:15 - 13:00'), ('VI', '13:00 - 13:45'),
    ('VII', '13:45 - 14:30'), ('VIII', '14:30 - 15:15'), ('IX', '15:15 - 16:00'),
]
SIGNATURE_URL = 'https://routine.tcioe.edu.np/api/signature'
SIGNATURE_PARAMS = {'department': 'DOECE', 'job_type': 'routine', 'faculty': 'BCT'}
SIGNATURE_ROLES = [
    ('Prepared_Routine', 'Prepared By:'), ('Checked_Routine', 'Checked By:'),
    ('Verified_Routine', 'Verified By:'), ('Approved_Routine', 'Approved By:'),
]
BS_MONTHS = ['Baisakh', 'Jestha', 'Ashadh', 'Shrawan', 'Bhadra', 'Ashwin',
             'Kartik', 'Mangsir', 'Poush', 'Magh', 'Falgun', 'Chaitra']
PDF_SEND_NAME = 'BCT_2081-AB_sem5_routine.pdf'
# every field normalize_cell keeps (except the slot itself) must be listed here
DIFF_FIELDS = [
    ('subject', 'subject'), ('subject_code', 'subject code'), ('room', 'room'),
    ('teachers', 'teacher'), ('labGroup', 'lab group'), ('start_time', 'start'),
    ('end_time', 'end'), ('periods', 'periods'), ('course_type', 'type'),
    ('isLab', 'lab'), ('dualLab', 'dual lab'), ('is_elective', 'elective'),
]
CAPTION_MAX = 1024  # Telegram document caption limit
MESSAGE_HEAD = ['ROUTINE CHANGED', '━━━━━━━━━━━━━━━━', 'BCT 2081-AB, Semester 5']
MAX_ATTEMPTS = 48  # about a day of retries at one per 30 min
FETCH_TIMEOUT = 30  # the campus and TU sites are sometimes slow
FETCH_RETRY_WAIT = 5
# the routine site is slower and flakier: wait longer and try three times
ROUTINE_TIMEOUT = 60
ROUTINE_RETRY_WAITS = (5, 20)
KEEP_ALERTS = 20  # finished alerts kept for their message ids
REPLACE_WINDOW = timedelta(hours=24)
REVERSED_TEXT = 'The earlier change was reversed. The routine is back to its previous version.'


async def get_with_retry(client, url, timeout=FETCH_TIMEOUT, waits=(FETCH_RETRY_WAIT,), **kwargs):
    """GET for the scrapers. Retries once per entry in waits after a timeout,
    connection error or 5xx, so a brief blip doesn't raise an operator alert."""
    for attempt in range(len(waits) + 1):
        last = attempt == len(waits)
        try:
            r = await client.get(url, timeout=timeout, **kwargs)
        except httpx.TransportError as e:
            if last:
                raise
            reason = type(e).__name__
        else:
            if r.status_code < 500 or last:
                r.raise_for_status()
                return r
            reason = f'HTTP {r.status_code}'
        print(f"fetch {url.split('/')[2]}: {reason}, retry {attempt + 1}/{len(waits)} in {waits[attempt]}s")
        await asyncio.sleep(waits[attempt])


async def fetch_routine(http):
    r = await get_with_retry(http, ROUTINE_URL, timeout=ROUTINE_TIMEOUT, waits=ROUTINE_RETRY_WAITS,
                             params=ROUTINE_PARAMS, headers=ROUTINE_HEADERS)
    cells = r.json().get('cells')
    if not cells:
        # never treat an empty or malformed reply as "every class removed"
        raise ValueError('routine API returned no cells')
    return cells


async def fetch_signatories(http):
    """(label, name, position) rows for the PDF. Empty on any failure, so a
    signature problem never holds back an alert."""
    try:
        r = await get_with_retry(http, SIGNATURE_URL, timeout=ROUTINE_TIMEOUT, waits=ROUTINE_RETRY_WAITS,
                                 params=SIGNATURE_PARAMS, headers=ROUTINE_HEADERS)
        rows = sorted(r.json().get('results') or [], key=lambda x: x.get('order') or 0)
        found = []
        for job, label in SIGNATURE_ROLES:
            person = next((x for x in rows if x.get('job') == job), None)
            if person:
                found.append((label, person.get('name') or '', person.get('position') or ''))
        return found
    except Exception as e:
        print(f'[ROUTINE] signatories unavailable: {type(e).__name__}')
        return []


def year_part(semester):
    """'sem5' -> ('Third', 'First', 'III/I'), or None."""
    try:
        n = int(str(semester).replace('sem', '')) - 1
        if n < 0:
            return None
        year, part = n // 2, n % 2
        return (['First', 'Second', 'Third', 'Fourth', 'Fifth'][year], ['First', 'Second'][part],
                f"{['I', 'II', 'III', 'IV', 'V'][year]}/{['I', 'II'][part]}")
    except (ValueError, IndexError):
        return None


def bs_date_label(value):
    """'2083-08-01' -> 'Mangsir 1, 2083'. The API date is already Nepali (BS)."""
    try:
        y, m, d = (int(x) for x in value.split('-'))
        if not 1 <= m <= 12:
            return ''
        return f'{BS_MONTHS[m - 1]} {d}, {y}'
    except (AttributeError, ValueError):
        return ''


def normalize_cell(c):
    """Keep only fields that reflect what a student actually sees."""
    subj, room = c.get('subject') or {}, c.get('room') or {}
    return {
        'day': c.get('day'), 'periodIndex': c.get('periodIndex'), 'periods': c.get('periods'),
        'teachers': sorted(t.get('name', '') for t in (c.get('teachers') or [])),
        'subject': subj.get('name'), 'subject_code': subj.get('code'), 'room': room.get('name'),
        'isLab': c.get('isLab'), 'labGroup': c.get('labGroup'), 'is_elective': c.get('is_elective'),
        'start_time': c.get('start_time'), 'end_time': c.get('end_time'),
        'course_type': c.get('course_type'), 'dualLab': c.get('dualLab'),
    }


def _day_rank(day):
    return DAY_ORDER.index(day) if day in DAY_ORDER else len(DAY_ORDER)


def normalized_cells(raw_cells):
    cells = [normalize_cell(c) for c in raw_cells]
    # full-content tiebreak: API ordering must not affect the hash
    cells.sort(key=lambda c: (_day_rank(c['day']), c['periodIndex'], json.dumps(c, sort_keys=True)))
    return cells


def content_hash(cells):
    return hashlib.sha256(json.dumps(cells, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _hm(t):
    return (t or '')[:5]


def _show(field, value):
    if isinstance(value, list):
        return ', '.join(value) or 'none'
    if field in ('start_time', 'end_time'):
        return _hm(value) or 'none'
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    return value if value not in (None, '') else 'none'


def _slot_label(c):
    group = f" group {c['labGroup']}" if c.get('labGroup') else ''
    return f"{c['day']} {_hm(c['start_time'])}-{_hm(c['end_time'])}{group}"


def diff_summary(old_cells, new_cells):
    """Plain-English lines for what changed. Identical cells are cancelled out
    first, so several classes sharing one slot can't hide each other."""
    removed, added = list(old_cells), []
    for cell in new_cells:
        if cell in removed:
            removed.remove(cell)
        else:
            added.append(cell)

    # pair an old and new cell in the same slot, closest match first
    matchers = [
        lambda o, n: o['subject'] == n['subject'] and o['labGroup'] == n['labGroup'],
        lambda o, n: o['subject'] == n['subject'],
        lambda o, n: o['labGroup'] == n['labGroup'],
    ]
    changed = []
    for match in matchers:
        for o in list(removed):
            for n in added:
                if (o['day'], o['periodIndex']) == (n['day'], n['periodIndex']) and match(o, n):
                    changed.append((o, n))
                    removed.remove(o)
                    added.remove(n)
                    break

    # the same change to the same subject becomes one line
    groups = {}
    for o, n in sorted(changed, key=lambda p: (_day_rank(p[0]['day']), p[0]['periodIndex'])):
        what = '; '.join(f'{label} changed from {_show(f, o.get(f))} to {_show(f, n.get(f))}'
                         for f, label in DIFF_FIELDS if o.get(f) != n.get(f))
        groups.setdefault((n['subject'] or o['subject'], what), []).append(o)
    entries = []
    for (subject, what), slots in groups.items():
        if len(slots) == 1:
            where = f'{_slot_label(slots[0])} ({subject})'
        else:
            days = ', '.join(dict.fromkeys(c['day'][:3] for c in slots))
            where = f'{subject}, {len(slots)} classes ({days})'
        entries.append((slots[0], f'{where}: {what}'))
    entries += [(o, f"{_slot_label(o)}: class removed, {o['subject']}") for o in removed]
    entries += [(n, f"{_slot_label(n)}: new class added, {n['subject']} "
                    f"({_show('room', n['room'])}, teacher: {_show('teachers', n['teachers'])})")
                for n in added]
    entries.sort(key=lambda e: (_day_rank(e[0]['day']), e[0]['periodIndex'], e[1]))
    return [line for _, line in entries]


def _room(name):
    """'Computer Lab #5 [D-106]' -> 'Room D-106'."""
    if name and name.endswith(']') and '[' in name:
        return f"Room {name[name.rindex('[') + 1:-1]}"
    return name or 'no room'


def _class_name(c):
    return c['subject'] + (f" group {c['labGroup']}" if c['labGroup'] else '')


def _match_old(new_cells, old_cells):
    """{index of new cell: (kind, old cell)} for classes that existed before."""
    left, matches = list(old_cells), {}

    def same_class(o, n):
        return (o['subject'], o['course_type'], o['labGroup']) == (n['subject'], n['course_type'], n['labGroup'])

    def same_start(o, n):
        return (o['day'], o['start_time']) == (n['day'], n['start_time'])

    # closest match first: identical, same time, moved within a day, moved to another day
    for kind, test in [
        ('same', lambda o, n: o == n),
        ('changed', lambda o, n: same_start(o, n) and same_class(o, n)),
        ('changed', lambda o, n: same_start(o, n) and o['subject'] == n['subject']),
        ('moved', lambda o, n: o['day'] == n['day'] and same_class(o, n)),
        ('moved', same_class),
    ]:
        for i, n in enumerate(new_cells):
            found = [o for o in left if i not in matches and test(o, n)]
            if found:
                o = min(found, key=lambda c: abs(c['periodIndex'] - n['periodIndex']))
                matches[i] = (kind, o)
                left.remove(o)
    return matches, left


def _label(kind, old, new):
    if kind == 'same':
        return ''
    skip = {'subject', 'start_time'}
    if kind == 'moved' or old['end_time'] != new['end_time']:
        skip.add('periods')
    if kind == 'moved':
        skip.add('end_time')
    diffs = [(f, name) for f, name in DIFF_FIELDS if f not in skip and old.get(f) != new.get(f)]
    # a code-only change matters; alongside other changes it is noise
    if len(diffs) > 1:
        diffs = [d for d in diffs if d[0] != 'subject_code']
    detail = ', '.join(f'{name} was {_show(f, old.get(f))}' for f, name in diffs)
    if kind == 'moved':
        day = '' if old['day'] == new['day'] else f"{old['day'][:3]} "
        return f" [MOVED from {day}{_hm(old['start_time'])}{'; ' + detail if detail else ''}]"
    return f' [CHANGED: {detail}]'


def build_messages(old_cells, new_cells):
    """The new timetable of each affected day, with what changed marked. Split
    into several messages, never cut off, if it exceeds the caption limit."""
    matches, removed = _match_old(new_cells, old_cells)
    affected = {c['day'] for c in removed}
    for i, n in enumerate(new_cells):
        kind, old = matches.get(i, ('new', None))
        if kind != 'same':
            affected.add(n['day'])
            if old:
                affected.add(old['day'])

    blocks = []
    for day in sorted(affected, key=_day_rank):
        title = f'Updated {day} timetable'
        today = sorted((i for i, c in enumerate(new_cells) if c['day'] == day),
                       key=lambda i: (new_cells[i]['start_time'] or '', new_cells[i]['labGroup'] or ''))
        entries = []
        for number, i in enumerate(today, 1):
            c = new_cells[i]
            kind, old = matches.get(i, ('new', None))
            tags = ', '.join(t for t in (c['course_type'], c['labGroup']) if t)
            entries.append(
                f"{number}. {_hm(c['start_time'])}-{_hm(c['end_time'])}: {c['subject']} ({tags})\n"
                f"   {_room(c['room'])} ({', '.join(c['teachers']) or 'no teacher'})"
                + (' [NEW]' if kind == 'new' else _label(kind, old, c)))
        gone = [f"Removed: {_class_name(c)}, {_hm(c['start_time'])}-{_hm(c['end_time'])}"
                for c in removed if c['day'] == day]
        # a class that left for another day is also noted on the day it left
        gone += [f"Moved to {new_cells[i]['day'][:3]}: {_class_name(old)}"
                 for i, (_, old) in sorted(matches.items())
                 if old['day'] == day and new_cells[i]['day'] != day]
        entries = entries or ['No classes']
        blocks.append((title, f'{title}\n\n' + entries[0], True))
        blocks += [(title, text, False) for text in entries[1:] + gone]

    limit = CAPTION_MAX - _tg_len(f'\n\n📎 {PDF_SEND_NAME}')
    messages = ['\n'.join(MESSAGE_HEAD)]
    for title, block, starts_day in blocks:
        joined = f'{messages[-1]}\n\n{block}'
        if _tg_len(joined) <= limit:
            messages[-1] = joined
        else:
            # a split in the middle of a day repeats which day it is
            day_line = '' if starts_day else f'{title} (continued)\n\n'
            messages.append(f'ROUTINE CHANGED (continued)\n\n{day_line}{block}')
    return messages


def _tg_len(text):
    return len(text.encode('utf-16-le')) // 2


def get_initials(name):
    return ''.join(p[0].upper() for p in name.replace('Dr.', '').strip().split() if p)


def generate_pdf(raw_cells, signatories=()):
    """Static HTML -> PDF via weasyprint, straight from the JSON. No browser."""
    academic = next((c['academic_class'] for c in raw_cells if c.get('academic_class')), {})
    parts = year_part(academic.get('semester') or ROUTINE_PARAMS['semester'])
    title_part = f', {parts[0]} Year {parts[1]} Part' if parts else ''
    year_line = f'Year/Part: <strong>{parts[0]} Year / {parts[1]} Part [{parts[2]}]</strong>' if parts else ''
    effective = bs_date_label(academic.get('effective_from'))
    effective_line = f'Effective From: <strong>{effective}</strong>' if effective else ''
    signature_cells = ''.join(
        f'<div class="sign"><div class="sign-role">{label}</div>'
        f'<div class="sign-role">{html_lib.escape(name)}</div><div>{html_lib.escape(position)}</div></div>'
        for label, name, position in signatories
    )
    signature_block = f'<div class="signatures">{signature_cells}</div>' if signature_cells else ''
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    legend = {}
    for c in raw_cells:
        for t in c.get('teachers', []):
            if t.get('name') and t['name'] not in legend:
                contact = f" ({t['contact_number']})" if t.get('contact_number') else ''
                legend[t['name']] = f"<strong>{get_initials(t['name'])}</strong>: {t['name']}{contact}"
    legend_str = ' &bull; '.join(sorted(legend.values()))

    head_cells = ''.join(
        f'<th class="period-header"><div class="period-num">{n}</div><div class="period-time">{t.replace(" ", "")}</div></th>'
        for n, t in PERIODS
    )
    body_rows = ''
    for day in days:
        row = f'<tr><td class="day-cell">{day}</td>'
        pi = 0
        while pi < len(PERIODS):
            matching = [c for c in raw_cells if c.get('day') == day and c.get('periodIndex') == pi]
            if matching:
                span = max(c.get('periods', 1) for c in matching)
                if len(matching) > 1 and all(c.get('course_type') == 'PR' for c in matching):
                    cards = ''
                    for c in matching:
                        room = (c.get('room') or {}).get('name') or f"Room {(c.get('room') or {}).get('room_no', '')}"
                        grp = f"{c['labGroup']} | " if c.get('labGroup') else ''
                        inits = ', '.join(get_initials(t['name']) for t in c.get('teachers', []))
                        cards += (f'<div class="concurrent-card"><div class="subj-name">{(c.get("subject") or {}).get("name", "")}</div>'
                                  f'<div class="subj-meta">{room} | <span class="badge-group">{grp}</span>{inits} <span class="badge-l">L</span></div></div>')
                    row += f'<td colspan="{span}" style="background:#f9fafb;padding:2px;height:52px;"><div class="concurrent-container">{cards}</div></td>'
                else:
                    c = matching[0]
                    room = (c.get('room') or {}).get('name') or f"Room {(c.get('room') or {}).get('room_no', '')}"
                    badge = '<span class="badge-l">L</span>' if c.get('course_type') == 'PR' else ''
                    inits = ', '.join(get_initials(t['name']) for t in c.get('teachers', []))
                    grp = f'<span class="badge-group">Group {c["labGroup"]}</span> | ' if c.get('labGroup') else ''
                    row += (f'<td colspan="{span}" style="padding:2px;height:52px;"><div class="single-cell">'
                            f'<div class="subj-name">{(c.get("subject") or {}).get("name", "")}</div>'
                            f'<div class="subj-meta">{room} {badge}</div><div class="subj-teacher">{grp}{inits}</div></div></td>')
                pi += span
            else:
                covered = any(c.get('periodIndex', 0) < pi < c.get('periodIndex', 0) + c.get('periods', 1) for c in raw_cells if c.get('day') == day)
                if not covered:
                    row += '<td class="cell-empty"></td>'
                pi += 1
        body_rows += row + '</tr>'

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@page {{ size: A4 landscape; margin: 6mm 8mm; }}
* {{ box-sizing: border-box; print-color-adjust: exact !important; }}
body {{ font-family: Arial, sans-serif; color: #111827; margin: 0; }}
.official-header {{ text-align: center; margin-bottom: 6px; }}
.title-tu {{ font-size: 14pt; font-weight: 800; }}
.title-sub {{ font-size: 11.5pt; font-weight: 700; }}
.title-dept {{ font-size: 11pt; font-weight: 700; color: #374151; }}
.title-routine {{ font-size: 10.5pt; font-weight: 800; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; table-layout: fixed; margin-top: 4px; }}
th, td {{ border: 1px solid #d1d5db; text-align: center; vertical-align: middle; }}
th.day-header, td.day-cell {{ width: 75px; background: #f9fafb; font-weight: 700; font-size: 8pt; }}
th.period-header {{ background: #f9fafb; padding: 4px 2px; }}
.period-num {{ font-size: 10pt; font-weight: 800; }}
.period-time {{ font-size: 7pt; color: #4b5563; margin-top: 2px; }}
.single-cell {{ height: 100%; display: flex; flex-direction: column; justify-content: center; padding: 3px; }}
.subj-name {{ font-size: 8pt; font-weight: 700; }}
.subj-meta {{ font-size: 7.5pt; margin-top: 2px; }}
.subj-teacher {{ font-size: 7pt; color: #4b5563; margin-top: 1.5px; }}
.badge-l {{ background: #dbeafe; color: #1e40af; font-size: 6.5pt; font-weight: 700; padding: 1px 3.5px; border-radius: 3px; margin-left: 3px; }}
.badge-group {{ font-weight: 600; color: #374151; }}
.concurrent-container {{ display: flex; flex-direction: column; gap: 3px; height: 100%; padding: 2.5px; }}
.concurrent-card {{ border: 1.5px solid #e5e7eb; border-radius: 4px; padding: 3px; display: flex; flex-direction: column; justify-content: center; flex: 1; }}
.legend-box {{ margin-top: 8px; border: 1px solid #e5e7eb; border-radius: 5px; background: #f9fafb; padding: 5px 8px; font-size: 7.5pt; color: #374151; }}
.meta {{ display: flex; justify-content: space-between; font-size: 9pt; margin-top: 8px; }}
.signatures {{ display: flex; gap: 8px; margin-top: 30px; }}
.sign {{ flex: 1; border-top: 1px solid #111827; padding-top: 3px; text-align: center; font-size: 8pt; font-style: italic; }}
.sign-role {{ font-weight: 700; }}
</style></head><body>
<div class="official-header">
  <div class="title-tu">Tribhuvan University</div>
  <div class="title-sub">Institute of Engineering</div>
  <div class="title-sub">Thapathali Campus</div>
  <div class="title-dept">Department of Electronics and Computer Engineering</div>
  <div class="title-routine">Class Routine [Bachelor of Computer Engineering{title_part} ({ROUTINE_PARAMS['section']})]</div>
</div>
<div class="meta"><div>{year_line}</div><div>{effective_line}</div></div>
<table><thead><tr><th class="day-header">Day/Period</th>{head_cells}</tr></thead>
<tbody>{body_rows}</tbody></table>
<div class="legend-box">{legend_str}</div>
{signature_block}
</body></html>"""

    return HTML(string=html).write_pdf()


async def send_telegram_alert(http, tg_token, chat_id, text, pdf_bytes):
    """Returns (message_id, retry). retry is True only when Telegram surely
    did not post it, so a retry can't create a duplicate."""
    base = f'https://api.telegram.org/bot{tg_token}'
    try:
        if pdf_bytes:
            r = await http.post(
                f'{base}/sendDocument',
                data={'chat_id': chat_id, 'caption': text},
                files={'document': (PDF_SEND_NAME, pdf_bytes, 'application/pdf')},
                timeout=60,
            )
        else:
            r = await http.post(f'{base}/sendMessage', data={'chat_id': chat_id, 'text': text}, timeout=30)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        print(f'[ROUTINE] telegram not reached: {type(e).__name__}')
        return None, True
    except Exception as e:
        print(f'[ROUTINE] telegram result unknown, not retrying: {type(e).__name__}')
        return None, False
    if r.status_code != 200:
        print(f'[ROUTINE] telegram HTTP {r.status_code}: {r.text[:200]}')
        return None, r.status_code < 500
    try:
        return r.json()['result']['message_id'], False
    except Exception:
        print('[ROUTINE] telegram sent but no message id returned')
        return None, False


def webhook_id(url):
    """Discord webhook id, without the secret token."""
    return url.split('?')[0].rstrip('/').split('/')[-2]


async def _save_now(state):
    # push immediately: a redeploy inside the debounce window would resend
    store.save_routine_state(state)
    await asyncio.to_thread(store.flush_gist_backup)


async def deliver_pending(http, state, raw_cells, tg_token, tg_chat_ids,
                          send_discord_text_file, webhooks):
    """Send each alert to the destinations still waiting for it, recording
    message ids in the same format manual notices use."""
    pending = [a for a in state.get('alerts', []) if a.get('waiting')]
    if not pending:
        return
    try:
        signatories = await fetch_signatories(http)
        pdf_bytes = await asyncio.to_thread(generate_pdf, raw_cells, signatories)
    except Exception as e:
        print(f'[ROUTINE] PDF failed, sending text only: {e}')
        pdf_bytes = None
    hooks = {webhook_id(w): w for w in webhooks if w}

    for alert in pending:
        alert['attempts'] = alert.get('attempts', 0) + 1
        text = alert.get('text') or '\n'.join(MESSAGE_HEAD + [''] + alert['changes'])[:CAPTION_MAX]
        attach = pdf_bytes if alert.get('pdf', True) else None
        if attach:
            text += f'\n\n📎 {PDF_SEND_NAME}'
        waiting, published = alert['waiting'], alert['published']

        for chat_id in list(waiting.get('telegram', [])):
            if not tg_token or chat_id not in [str(c) for c in tg_chat_ids]:
                waiting['telegram'].remove(chat_id)
                continue
            mid, retry = await send_telegram_alert(http, tg_token, chat_id, text, attach)
            if mid:
                published['telegram'].append({'chat_id': chat_id, 'message_ids': [mid]})
            if not retry:
                waiting['telegram'].remove(chat_id)
            await _save_now(state)

        for hook in list(waiting.get('discord', [])):
            if not send_discord_text_file or hook not in hooks:
                waiting['discord'].remove(hook)
                continue
            unsure = []
            try:
                refs = await send_discord_text_file(
                    text, file_bytes=attach,
                    filename=PDF_SEND_NAME if attach else None,
                    only_webhooks=[hooks[hook]], unsure=unsure,
                )
            except Exception as e:
                print(f'[ROUTINE] discord result unknown, not retrying: {type(e).__name__}')
                refs, unsure = [], [hook]
            published['discord'] += refs
            if refs or unsure:
                waiting['discord'].remove(hook)
            await _save_now(state)

        left = len(waiting.get('telegram', [])) + len(waiting.get('discord', []))
        if not left:
            alert.pop('waiting')
        elif alert['attempts'] >= MAX_ATTEMPTS:
            print(f'[ROUTINE] giving up on {left} destination(s) after {MAX_ATTEMPTS} tries')
            alert.pop('waiting')
        else:
            print(f'[ROUTINE] {left} destination(s) failed, will retry next check')
        print(f"[ROUTINE] delivered: telegram={len(published['telegram'])} "
              f"discord={len(published['discord'])}")
        await _save_now(state)


def _last_batch(alerts):
    """The parts of the latest alert (only the last part has the PDF)."""
    start = len(alerts) - 1
    while start > 0 and alerts[start - 1].get('pdf') is False:
        start -= 1
    return alerts[start:] if alerts else []


def _take_over(batch, now):
    """(base cells, since, message ids to delete) if the latest alert can be replaced."""
    first = batch[0] if batch else {}
    if not first.get('base') or now - datetime.fromisoformat(first['since']) >= REPLACE_WINDOW:
        return None
    old = {'telegram': [], 'discord': []}
    for alert in batch:
        for source in (alert['published'], alert.get('replaces') or {}):
            for kind in old:
                old[kind] += source.get(kind) or []
    return first['base'], first['since'], old


async def delete_replaced(http, state, tg_token, tg_chat_ids, delete_discord, webhooks):
    """Delete a replaced alert, per destination, only once every part of the
    new one is confirmed there. One attempt each."""
    batch = _last_batch(state.get('alerts') or [])
    old = batch[0].get('replaces') if batch else None
    if not old:
        return

    def has_all(kind, dest):
        key = (lambda r: r['chat_id']) if kind == 'telegram' else (lambda r: webhook_id(r['webhook_url']))
        return all(any(key(r) == dest for r in a['published'][kind]) for a in batch)

    changed = False
    for ref in list(old['telegram']):
        if not has_all('telegram', ref['chat_id']):
            continue
        for mid in ref['message_ids']:
            try:
                r = await http.post(f'https://api.telegram.org/bot{tg_token}/deleteMessage',
                                    data={'chat_id': ref['chat_id'], 'message_id': mid}, timeout=30)
                if r.status_code != 200:
                    print(f'[ROUTINE] old telegram alert not deleted: HTTP {r.status_code}')
            except Exception as e:
                print(f'[ROUTINE] old telegram alert not deleted: {type(e).__name__}')
        old['telegram'].remove(ref)
        changed = True
    for ref in list(old['discord']):
        if not delete_discord or not has_all('discord', webhook_id(ref['webhook_url'])):
            continue
        if not await delete_discord(ref['webhook_url'], ref['message_id']):
            print('[ROUTINE] old discord alert not deleted')
        old['discord'].remove(ref)
        changed = True
    if changed:
        await _save_now(state)


async def check_and_notify(http, tg_token, tg_chat_ids, send_discord_text_file=None, webhooks=(),
                           delete_discord=None):
    """Fetch, compare with the saved baseline, and alert on an actual change.
    The first run only saves the baseline."""
    raw_cells = await fetch_routine(http)
    cells = normalized_cells(raw_cells)
    h = content_hash(cells)

    state = store.load_routine_state()
    if not state:
        store.save_routine_state({'hash': h, 'cells': cells})
        print(f'[ROUTINE] first run, saved baseline, {len(cells)} cells. Nothing sent.')
        return

    if state.get('hash') == h:
        print(f'[ROUTINE] no change, {len(cells)} cells.')
    else:
        state_before = state.get('cells') or []
        changes = diff_summary(state_before, cells)
        state.update(hash=h, cells=cells)
        if changes:
            print(f'[ROUTINE] CHANGE DETECTED: {len(changes)} change(s)')
            for line in changes:
                print(' ', line)
            now = datetime.now(timezone.utc)
            alerts = state.get('alerts', [])
            batch = _last_batch(alerts)
            replaced = _take_over(batch, now)
            if replaced:
                # one combined alert, compared with the version before the one it replaces
                base, since, old_ids = replaced
                alerts = alerts[:len(alerts) - len(batch)]
            else:
                base, since, old_ids = state_before, now.isoformat(), None
            if diff_summary(base, cells):
                texts = build_messages(base, cells)
            else:
                texts = ['\n'.join(MESSAGE_HEAD + ['', REVERSED_TEXT])]
            for a in alerts:
                a.pop('base', None)  # only the latest alert needs it
            new_alerts = [{
                'detected_at': now.isoformat(),
                'changes': changes if i == 0 else [],
                'text': text,
                'pdf': i == len(texts) - 1,
                'waiting': {
                    'telegram': [str(c) for c in tg_chat_ids] if tg_token else [],
                    'discord': [webhook_id(w) for w in webhooks if w] if send_discord_text_file else [],
                },
                'published': {'telegram': [], 'discord': []},
            } for i, text in enumerate(texts)]
            new_alerts[0].update(base=base, since=since)
            if old_ids:
                new_alerts[0]['replaces'] = old_ids
            alerts = alerts + new_alerts
            old = [a for a in alerts if not a.get('waiting')][:-KEEP_ALERTS]
            state['alerts'] = [a for a in alerts if a not in old]
        else:
            print('[ROUTINE] hash differs but no visible change, skip send')
        # queued before sending, so a failed send is retried instead of lost
        await _save_now(state)

    await deliver_pending(http, state, raw_cells, tg_token, tg_chat_ids,
                          send_discord_text_file, webhooks)
    await delete_replaced(http, state, tg_token, tg_chat_ids, delete_discord, webhooks)
