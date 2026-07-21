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
    """Deadline cannot be before today (NPT). Today and tomorrow are OK."""
    today = today_npt()
    if ad_date < today:
        raise ValueError(
            f'Deadline cannot be in the past (today is {today.isoformat()} / {today.strftime("%A")}).'
        )
    return ad_date


def parse_publish_at_npt(text):
    """Parse YYYY-MM-DD HH:MM (optional :SS) in Nepal time."""
    text = (text or '').strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=NPT)
        except ValueError:
            continue
    raise ValueError('Use YYYY-MM-DD HH:MM (24h, Nepal time).')


def require_future_publish_at(dt):
    now = now_npt()
    if dt <= now:
        raise ValueError(
            f'Must be in the future (now {now.strftime("%Y-%m-%d %H:%M")} NPT).'
        )
    return dt


def format_publish_at(value):
    if isinstance(value, str):
        dt = datetime.fromisoformat(value)
    else:
        dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NPT)
    else:
        dt = dt.astimezone(NPT)
    return dt.strftime('%Y-%m-%d %H:%M')


def scheduled_ready(rows, now=None):
    """Notices due to publish now (status=scheduled, publish_at <= now)."""
    now = now or now_npt()
    out = []
    for r in rows:
        if r.get('status') != 'scheduled':
            continue
        raw = r.get('publish_at')
        if not raw:
            continue
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NPT)
        if dt <= now:
            out.append(r)
    return out


def is_expired(ad_deadline_str, now=None):
    """Expired after end of deadline day in Nepal time."""
    now = now or now_npt()
    ad = date.fromisoformat(ad_deadline_str)
    end = datetime.combine(ad, time(23, 59, 59), tzinfo=NPT)
    return now > end


def newly_expired_rows(rows):
    """Mark newly past-deadline assignments expired. Returns those notices."""
    newly = []
    for r in rows:
        if r.get('category') != 'assignment':
            continue
        if r.get('status') in ('expired', 'scheduled'):
            continue
        dl = r.get('deadline_ad')
        if dl and is_expired(dl):
            r['status'] = 'expired'
            newly.append(r)
    return newly


def mark_expired_rows(rows):
    return bool(newly_expired_rows(rows))


def deadlines_tomorrow(rows, now=None):
    """Assignments whose deadline date is tomorrow (NPT)."""
    now = now or now_npt()
    tomorrow = (now.date() + timedelta(days=1)).isoformat()
    out = []
    for r in rows:
        if r.get('category') != 'assignment':
            continue
        if r.get('status') in ('expired', 'scheduled'):
            continue
        if r.get('deadline_ad') == tomorrow:
            out.append(r)
    return out


def seconds_until_next_6pm_npt(now=None):
    now = now or now_npt()
    target = datetime.combine(now.date(), time(18, 0, 0), tzinfo=NPT)
    if now >= target:
        target = target + timedelta(days=1)
    # ceil-ish so we don't wake a fraction of a second before 18:00
    secs = (target - now).total_seconds()
    return max(1, int(secs) + 1)
