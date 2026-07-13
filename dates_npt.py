# Nepal time + AD/BS deadline helpers
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo

try:
    import nepali_datetime as nep_dt
except ImportError:
    nep_dt = None

NPT = ZoneInfo('Asia/Kathmandu')


def now_npt():
    return datetime.now(NPT)


def today_npt():
    return now_npt().date()


def parse_yyyy_mm_dd(text):
    text = (text or '').strip()
    try:
        y, m, d = (int(p) for p in text.split('-'))
        return y, m, d
    except Exception:
        raise ValueError('Use format YYYY-MM-DD')


def ad_from_input(text):
    y, m, d = parse_yyyy_mm_dd(text)
    try:
        return date(y, m, d)
    except ValueError as exc:
        raise ValueError('Not a real calendar date.') from exc


def bs_to_ad(text):
    if nep_dt is None:
        raise ValueError('nepali-datetime not installed')
    y, m, d = parse_yyyy_mm_dd(text)
    try:
        bs = nep_dt.date(y, m, d)
    except Exception as exc:
        raise ValueError('Not a real BS date.') from exc
    return bs.to_datetime_date()


def ad_to_bs_str(ad_date):
    if nep_dt is None:
        return 'BS unavailable'
    bs = nep_dt.date.from_datetime_date(ad_date)
    return f'{bs.year:04d}-{bs.month:02d}-{bs.day:02d}'


def format_deadline_pair(ad_date):
    day = ad_date.strftime('%A')  # e.g. Wednesday
    return f'AD {ad_date.isoformat()} ({day})  |  BS {ad_to_bs_str(ad_date)}'


def format_deadline_short(ad_date_or_str):
    """Compact for List/Status lines: 2026-07-17 (Friday)."""
    if isinstance(ad_date_or_str, str):
        ad_date = date.fromisoformat(ad_date_or_str)
    else:
        ad_date = ad_date_or_str
    return f'{ad_date.isoformat()} ({ad_date.strftime("%A")})'


def require_future_deadline(ad_date):
    """Deadline must be after today (NPT). Raises ValueError if not."""
    today = today_npt()
    if ad_date <= today:
        raise ValueError(
            f'Deadline must be after today ({today.isoformat()} / {today.strftime("%A")}).'
        )
    return ad_date


def is_expired(ad_deadline_str, now=None):
    """Expired after end of deadline day in Nepal time."""
    now = now or now_npt()
    ad = date.fromisoformat(ad_deadline_str)
    end = datetime.combine(ad, time(23, 59, 59), tzinfo=NPT)
    return now > end


def mark_expired_rows(rows):
    changed = False
    for r in rows:
        if r.get('category') != 'assignment':
            continue
        if r.get('status') == 'expired':
            continue
        dl = r.get('deadline_ad')
        if dl and is_expired(dl):
            r['status'] = 'expired'
            changed = True
    return changed


def deadlines_tomorrow(rows, now=None):
    """Assignments whose deadline date is tomorrow (NPT)."""
    now = now or now_npt()
    tomorrow = (now.date() + timedelta(days=1)).isoformat()
    out = []
    for r in rows:
        if r.get('category') != 'assignment':
            continue
        if r.get('status') == 'expired':
            continue
        if r.get('deadline_ad') == tomorrow:
            out.append(r)
    return out


def seconds_until_next_6pm_npt(now=None):
    now = now or now_npt()
    target = datetime.combine(now.date(), time(18, 0, 0), tzinfo=NPT)
    if now >= target:
        target = target + timedelta(days=1)
    return max(1, int((target - now).total_seconds()))
