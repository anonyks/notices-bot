# BCT 2081-AB sem5 class routine change tracker.
# First run only establishes a baseline (sends nothing); later runs notify
# (Telegram + Discord, like any other notice) only when the routine content
# actually changed. State persists via store.py so it survives Render redeploys.
import hashlib
import json

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
PDF_SEND_NAME = 'BCT_2081-AB_sem5_routine.pdf'
PDF_FIELDS = [
    ('start_time', 'start'), ('end_time', 'end'), ('subject', 'subject'),
    ('room', 'room'), ('teachers', 'teachers'), ('course_type', 'type'),
    ('periods', 'periods'), ('isLab', 'lab'), ('dualLab', 'dual-lab'), ('is_elective', 'elective'),
]


async def fetch_routine(http):
    r = await http.get(ROUTINE_URL, params=ROUTINE_PARAMS, headers=ROUTINE_HEADERS, timeout=20)
    r.raise_for_status()
    return r.json().get('cells') or []


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


def normalized_cells(raw_cells):
    cells = [normalize_cell(c) for c in raw_cells]
    cells.sort(key=lambda c: (DAY_ORDER.index(c['day']), c['periodIndex']))
    return cells


def content_hash(cells):
    return hashlib.sha256(json.dumps(cells, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _hm(t):
    return (t or '')[:5]


def diff_summary(old_cells, new_cells):
    """One plain-English line per changed slot, naming exactly which fields
    changed (so a time/duration/lab-flag-only change is still visible, not
    just subject/room/teacher)."""
    def key(c):
        return (c['day'], c['periodIndex'], c['labGroup'])

    old_map, new_map = {key(c): c for c in old_cells}, {key(c): c for c in new_cells}
    lines = []
    for k in sorted(set(old_map) | set(new_map), key=lambda k: (DAY_ORDER.index(k[0]), k[1])):
        o, n = old_map.get(k), new_map.get(k)
        if o == n:
            continue
        day = k[0]
        if o is None:
            lines.append(f"{day} {_hm(n['start_time'])}-{_hm(n['end_time'])}: new class added, {n['subject']}")
        elif n is None:
            lines.append(f"{day} {_hm(o['start_time'])}-{_hm(o['end_time'])}: class removed, {o['subject']}")
        else:
            subj = n['subject'] or o['subject']
            parts = [f'{label} changed from {o[f]} to {n[f]}' for f, label in PDF_FIELDS if o[f] != n[f]]
            lines.append(f"{day} {_hm(o['start_time'])}-{_hm(o['end_time'])} ({subj}): " + '; '.join(parts))
    return lines


def build_message(changes):
    return '\n'.join(['ROUTINE CHANGED', '━━━━━━━━━━━━━━━━', 'BCT 2081-AB, Semester 5', '']
                      + changes[:30] + ['', f'📎 {PDF_SEND_NAME}'])


def get_initials(name):
    return ''.join(p[0].upper() for p in name.replace('Dr.', '').strip().split() if p)


def generate_pdf(raw_cells):
    """Static HTML -> PDF via weasyprint, straight from the JSON. No browser."""
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    legend = {}
    for c in raw_cells:
        for t in c.get('teachers', []):
            if t.get('name') and t['name'] not in legend:
                contact = f" ({t['contact_number']})" if t.get('contact_number') else ''
                legend[t['name']] = f"<strong>{get_initials(t['name'])}</strong>: {t['name']}{contact}"
    legend_str = ' &bull; '.join(sorted(legend.values()))

    head_cells = ''.join(
        f'<th class="period-header"><div class="period-num">{n}</div><div class="period-time">{t}</div></th>'
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
                        grp = f"Group {c['labGroup']} | " if c.get('labGroup') else ''
                        inits = ' + '.join(get_initials(t['name']) for t in c.get('teachers', []))
                        cards += (f'<div class="concurrent-card"><div class="subj-name">{(c.get("subject") or {}).get("name", "")}</div>'
                                  f'<div class="subj-meta">{room} | <span class="badge-group">{grp}</span>{inits} <span class="badge-l">L</span></div></div>')
                    row += f'<td colspan="{span}" style="background:#f9fafb;padding:2px;height:52px;"><div class="concurrent-container">{cards}</div></td>'
                else:
                    c = matching[0]
                    room = (c.get('room') or {}).get('name') or f"Room {(c.get('room') or {}).get('room_no', '')}"
                    badge = '<span class="badge-l">L</span>' if c.get('course_type') == 'PR' else ''
                    inits = ' + '.join(get_initials(t['name']) for t in c.get('teachers', []))
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
th.day-header, td.day-cell {{ width: 75px; background: #f9fafb; font-weight: 700; font-size: 9pt; }}
th.period-header {{ background: #f9fafb; padding: 4px 2px; }}
.period-num {{ font-size: 11pt; font-weight: 800; }}
.period-time {{ font-size: 7.5pt; color: #4b5563; margin-top: 2px; }}
.single-cell {{ height: 100%; display: flex; flex-direction: column; justify-content: center; padding: 3px; }}
.subj-name {{ font-size: 9.5pt; font-weight: 700; }}
.subj-meta {{ font-size: 8.5pt; margin-top: 2px; }}
.subj-teacher {{ font-size: 8pt; color: #4b5563; margin-top: 1.5px; }}
.badge-l {{ background: #dbeafe; color: #1e40af; font-size: 7pt; font-weight: 700; padding: 1px 3.5px; border-radius: 3px; margin-left: 3px; }}
.badge-group {{ font-weight: 600; color: #374151; }}
.concurrent-container {{ display: flex; flex-direction: column; gap: 3px; height: 100%; padding: 2.5px; }}
.concurrent-card {{ border: 1.5px solid #e5e7eb; border-radius: 4px; padding: 3px; display: flex; flex-direction: column; justify-content: center; flex: 1; }}
.legend-box {{ margin-top: 8px; border: 1px solid #e5e7eb; border-radius: 5px; background: #f9fafb; padding: 5px 8px; font-size: 7.5pt; color: #374151; }}
</style></head><body>
<div class="official-header">
  <div class="title-tu">Tribhuvan University</div>
  <div class="title-sub">Institute of Engineering</div>
  <div class="title-sub">Thapathali Campus</div>
  <div class="title-dept">Department of Electronics and Computer Engineering</div>
  <div class="title-routine">Class Routine [Bachelor of Computer Engineering, Third Year First Part (AB)]</div>
</div>
<table><thead><tr><th class="day-header">Day/Period</th>{head_cells}</tr></thead>
<tbody>{body_rows}</tbody></table>
<div class="legend-box"><strong>Teachers: </strong>{legend_str}</div>
</body></html>"""

    return HTML(string=html).write_pdf()


async def send_telegram_document(http, tg_token, tg_chat_ids, caption, pdf_bytes, filename):
    posted_any = False
    for chat_id in tg_chat_ids:
        try:
            r = await http.post(
                f'https://api.telegram.org/bot{tg_token}/sendDocument',
                data={'chat_id': chat_id, 'caption': caption},
                files={'document': (filename, pdf_bytes, 'application/pdf')},
                timeout=60,
            )
            if r.status_code == 200:
                posted_any = True
            else:
                print(f'routine telegram HTTP {r.status_code}: {r.text[:200]}')
        except Exception as e:
            print(f'routine telegram error: {e}')
    return posted_any


async def check_and_notify(http, tg_token, tg_chat_ids, send_discord_text_file=None):
    """Fetch, hash, compare vs saved baseline. Notify (Telegram + Discord,
    like any other notice) only on an actual change. First run ever just
    saves the baseline and returns -- never sends."""
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
        return

    changes = diff_summary(state.get('cells') or [], cells)
    if not changes:
        # hash differed but no actual cell content differs (e.g. hashing
        # logic changed between deploys) -- nothing real to report, skip.
        print('[ROUTINE] hash differs but no visible cell change, skip send')
        store.save_routine_state({'hash': h, 'cells': cells})
        return

    print(f'[ROUTINE] CHANGE DETECTED: {len(changes)} slot(s) differ')
    for line in changes:
        print(' ', line)

    pdf_bytes = generate_pdf(raw_cells)
    text = build_message(changes)

    if tg_token and tg_chat_ids:
        tg_ok = await send_telegram_document(http, tg_token, tg_chat_ids, text, pdf_bytes, PDF_SEND_NAME)
        print(f'[ROUTINE] telegram send {"ok" if tg_ok else "FAILED"}')
    else:
        print('[ROUTINE] no Telegram token/chat configured, skipping send')

    if send_discord_text_file:
        try:
            disc_refs = await send_discord_text_file(text, file_bytes=pdf_bytes, filename=PDF_SEND_NAME)
            print(f'[ROUTINE] discord send {"ok" if disc_refs else "FAILED"}')
        except Exception as e:
            print(f'[ROUTINE] discord error: {e}')

    store.save_routine_state({'hash': h, 'cells': cells})
