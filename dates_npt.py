# Nepal time + AD/BS deadline helpers
import re
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo

try:
    import nepali_datetime as nep_dt
except ImportError:
    nep_dt = None

NPT = ZoneInfo('Asia/Kathmandu')

DEADLINE_CATEGORIES = ('assignment', 'general')
DEFAULT_DEADLINE_TIME = '23:59'


def now_npt():
    return datetime.now(NPT)


def today_npt():
    return now_npt().date()


def parse_yyyy_mm_dd(text):
    text = (text or '').strip().rstrip('.,;')
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


def parse_deadline_time(text):
    """'17:00', '5pm' or '5:30 PM' -> 'HH:MM' (24h)."""
    cleaned = (text or '').strip().lower().replace('.', '')
    m = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', cleaned)
    if not m or (m.group(2) is None and m.group(3) is None):
        raise ValueError('Use a time like 11:59 PM, 5pm or 17:00.')
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if half:
        if not 1 <= hour <= 12:
            raise ValueError('With AM/PM the hour must be 1 to 12.')
        hour = hour % 12 + (12 if half == 'pm' else 0)
    if hour > 23 or minute > 59:
        raise ValueError('Not a real time.')
    return f'{hour:02d}:{minute:02d}'


def format_time_12h(value):
    hour, minute = (int(x) for x in (value or DEFAULT_DEADLINE_TIME).split(':'))
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def deadline_moment(ad_date, deadline_time=None):
    """Last moment of the deadline minute, Nepal time. No time means 11:59 PM."""
    if isinstance(ad_date, str):
        ad_date = date.fromisoformat(ad_date)
    hour, minute = (int(x) for x in (deadline_time or DEFAULT_DEADLINE_TIME).split(':'))
    return datetime.combine(ad_date, time(hour, minute, 59), tzinfo=NPT)


def require_future_deadline_time(ad_date, deadline_time=None):
    if deadline_moment(ad_date, deadline_time) < now_npt():
        raise ValueError(
            f'{format_time_12h(deadline_time)} on that day has already passed '
            f'(now {now_npt().strftime("%Y-%m-%d %H:%M")} NPT).'
        )


def has_deadline(r):
    return r.get('category') in DEADLINE_CATEGORIES and bool(r.get('deadline_ad'))


def format_deadline_pair(ad_date, deadline_time=None):
    day = ad_date.strftime('%A')  # e.g. Wednesday
    at = f', {format_time_12h(deadline_time)}' if deadline_time else ''
    return f'AD {ad_date.isoformat()} ({day}){at}  |  BS {ad_to_bs_str(ad_date)}'


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
    if ad_date.year == today.year + 1:
        fixed = ad_date.replace(year=today.year)
        if fixed >= today:
            raise ValueError(
                f'Year looks wrong — did you mean {fixed.isoformat()}? '
                f'You entered {ad_date.isoformat()}.'
            )
    if ad_date.year > today.year + 1:
        raise ValueError('Deadline year looks too far ahead (check the year).')
    if ad_date < today:
        raise ValueError(
            f'Deadline cannot be in the past (today is {today.isoformat()} / {today.strftime("%A")}).'
        )
    return ad_date


def require_schedule_before_deadline(publish_at, deadline_ad, deadline_time=None):
    """Scheduled post must not be after the deadline."""
    if not deadline_ad or not publish_at:
        return
    dl = date.fromisoformat(deadline_ad) if isinstance(deadline_ad, str) else deadline_ad
    end = deadline_moment(dl, deadline_time)
    if isinstance(publish_at, str):
        pub = datetime.fromisoformat(publish_at)
    else:
        pub = publish_at
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=NPT)
    else:
        pub = pub.astimezone(NPT)
    if pub > end:
        raise ValueError(
            f'Post time ({format_publish_at(pub)}) is after the deadline '
            f'({dl.isoformat()} {format_time_12h(deadline_time)}). '
            'Schedule earlier or change the deadline.'
        )


def parse_publish_at_npt(text):
    """Parse YYYY-MM-DD HH:MM (optional :SS) in Nepal time."""
    text = (text or '').strip().rstrip('.,;')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=NPT)
        except ValueError:
            continue
    raise ValueError('Use YYYY-MM-DD HH:MM (24h, Nepal time).')


def require_future_publish_at(dt):
    now = now_npt()
    if dt.year == now.year + 1:
        fixed = dt.replace(year=now.year)
        if fixed > now:
            raise ValueError(
                f'Year looks wrong — did you mean {fixed.strftime("%Y-%m-%d %H:%M")} NPT? '
                f'You entered {dt.strftime("%Y-%m-%d %H:%M")}.'
            )
    if dt.year > now.year + 1:
        raise ValueError('Too far in the future (check the year).')
    if dt <= now:
        entered = dt.strftime('%Y-%m-%d %H:%M')
        if dt.date() == now.date():
            raise ValueError(
                f'That time is already in the past today (now {now.strftime("%H:%M")} NPT). '
                f'You entered {entered}. Pick a later time — 24h format (20:08 = 8:08 PM).'
            )
        raise ValueError(
            f'That date/time is in the past (now {now.strftime("%Y-%m-%d %H:%M")} NPT). '
            f'You entered {entered}.'
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


def is_expired(ad_deadline_str, now=None, deadline_time=None):
    """Expired once the deadline minute has passed in Nepal time."""
    now = now or now_npt()
    return now > deadline_moment(ad_deadline_str, deadline_time)


def newly_expired_rows(rows):
    """Mark newly past-deadline notices expired. Returns those notices."""
    newly = []
    for r in rows:
        if r.get('category') not in DEADLINE_CATEGORIES:
            continue
        if r.get('status') in ('expired', 'scheduled'):
            continue
        dl = r.get('deadline_ad')
        if dl and is_expired(dl, deadline_time=r.get('deadline_time')):
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
        if r.get('category') not in DEADLINE_CATEGORIES:
            continue
        if r.get('status') in ('expired', 'scheduled'):
            continue
        if r.get('deadline_ad') == tomorrow:
            out.append(r)
    return out


def deadlines_today(rows, now=None):
    """Assignments whose deadline date is today (NPT)."""
    now = now or now_npt()
    today = now.date().isoformat()
    out = []
    for r in rows:
        if r.get('category') not in DEADLINE_CATEGORIES:
            continue
        if r.get('status') in ('expired', 'scheduled'):
            continue
        if r.get('deadline_ad') == today:
            out.append(r)
    return out


def already_reminded_for_deadline(r):
    """True if we already sent a 6pm reminder for this notice's current deadline."""
    dl = r.get('deadline_ad')
    return bool(dl) and r.get('reminded_for_deadline') == dl


def deadlines_for_reminder(rows, now=None):
    """
    6pm reminder once per notice/deadline:
    - due tomorrow (normal heads-up)
    - due today only if never reminded for this deadline (e.g. posted same day)
    Skip items already reminded yesterday as "tomorrow".
    """
    now = now or now_npt()
    out = []
    for r in deadlines_today(rows, now=now) + deadlines_tomorrow(rows, now=now):
        if already_reminded_for_deadline(r):
            continue
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
