# Telegram menu / wizard for manual notices
import json
from copy import deepcopy
from datetime import date, timedelta

import store
import dates_npt as dn

# per-chat wizard state: { chat_id: {step, draft, ...} }
sessions = {}
waiting_for_post = {}  # chat_id -> True after /post


def parse_chat_ids(raw):
    ids = []
    for part in (raw or '').split(','):
        part = part.strip()
        if part:
            ids.append(part)
    return ids


def allowed(chat_id, chat_ids):
    return str(chat_id) in {str(c) for c in chat_ids}


def main_reply_keyboard():
    return {
        'keyboard': [
            [{'text': 'Create'}, {'text': 'List'}],
            [{'text': 'Edit'}, {'text': 'Delete'}],
            [{'text': 'Status'}],
        ],
        'resize_keyboard': True,
        'is_persistent': True,
    }


def inline(rows):
    return {'inline_keyboard': rows}


def cat_emoji(cat):
    return {
        'general': '📢',
        'assignment': '📝',
        'urgent': '🚨',
        'from_site': '🌐',
    }.get(cat, '📌')


def notice_label(n, limit=48):
    """Human label for toasts / lists — prefer title over m1."""
    if not n:
        return '?'
    title = (n.get('title') or '').strip()
    if title:
        return title if len(title) <= limit else title[: limit - 1] + '…'
    return n.get('id') or '?'


def with_top_gap(text):
    """Leading blank line so a new post separates from the message above."""
    t = text or ''
    return t if t.startswith('\n') else '\n' + t


def format_from_site(title, url):
    """Same text for Telegram + Discord scraped posts."""
    title = (title or '').strip() or '(no title)'
    url = (url or '').strip()
    lines = [
        'FROM SITE',
        '━━━━━━━━━━━━━━━━',
        title,
    ]
    if url:
        lines += ['', url]
    return with_top_gap('\n'.join(lines))


def format_notice_text(n):
    """Full notice text — same on Telegram preview/follow-up and Discord."""
    cat = n.get('category', 'general')
    title = (n.get('title') or '').strip() or '(no title)'
    body = (n.get('body') or '').strip()
    link = (n.get('link') or '').strip()

    if cat == 'from_site':
        return format_from_site(title, link)

    if cat == 'urgent':
        lines = [
            'URGENT NOTICE',
            '━━━━━━━━━━━━━━━━',
            title,
        ]
        if body:
            lines += ['', body]
        lines.append('━━━━━━━━━━━━━━━━')
    elif cat == 'assignment':
        expired = n.get('status') == 'expired'
        lines = [
            'ASSIGNMENT  ·  expired_' if expired else 'ASSIGNMENT',
            '━━━━━━━━━━━━━━━━',
            title,
        ]
        if body:
            lines += ['', body]
    else:
        lines = [
            'GENERAL NOTICE',
            '━━━━━━━━━━━━━━━━',
            title,
        ]
        if body:
            lines += ['', body]

    if cat == 'assignment' and n.get('deadline_ad'):
        lines += ['', f"Deadline: {dn.format_deadline_pair(date.fromisoformat(n['deadline_ad']))}"]
        if n.get('status') == 'expired':
            lines.append('Status: expired_')
    if n.get('file_name'):
        lines += ['', f"File: {n.get('file_name')}"]
    elif n.get('file_type'):
        lines += ['', f"Attachment: {n.get('file_type')}"]
    return with_top_gap('\n'.join(lines))


def format_caption(n):
    """Short TG media caption when full text is too long for one caption."""
    cat = n.get('category', 'general')
    title = (n.get('title') or '').strip() or '(no title)'
    expired = n.get('status') == 'expired'
    if cat == 'urgent':
        head = 'URGENT NOTICE'
    elif cat == 'assignment':
        head = 'ASSIGNMENT  ·  expired_' if expired else 'ASSIGNMENT'
    elif cat == 'from_site':
        head = 'FROM SITE'
    else:
        head = 'GENERAL NOTICE'
    lines = [head, '━━━━━━━━━━━━━━━━', title]
    if cat == 'assignment' and n.get('deadline_ad'):
        lines.append(f"Deadline: {dn.format_deadline_pair(date.fromisoformat(n['deadline_ad']))}")
        if expired:
            lines.append('Status: expired_')
    if (n.get('body') or '').strip():
        lines.append('(full text in next message)')
    return with_top_gap('\n'.join(lines))[:1024]


def tg_media_parts(notice):
    """Caption (+ optional follow-up). One TG message when full text fits in 1024."""
    full = format_notice_text(notice)[:4096]
    if len(full) <= 1024:
        return full, None
    return format_caption(notice), full


def format_reminder_bundle(items):
    # Avoid "1." markdown lists — Discord glues the next line onto the title.
    lines = [
        '⏰ REMINDER',
        '━━━━━━━━━━━━━━━━',
        'Due TOMORROW',
        '',
    ]
    for i, n in enumerate(items, 1):
        title = n.get('title', 'Untitled')
        lines.append(f'{i}) {title}')
        lines.append(dn.format_deadline_pair(date.fromisoformat(n['deadline_ad'])))
        lines.append('')
    lines.append('━━━━━━━━━━━━━━━━')
    lines.append('Finish it before midnight.')
    return with_top_gap('\n'.join(lines))


def format_next_reminder_line(now=None):
    now = now or dn.now_npt()
    secs = dn.seconds_until_next_6pm_npt(now)
    hrs, rem = divmod(secs, 3600)
    mins = rem // 60
    if now.hour < 18:
        return f'Next reminder: today 6:00 PM (in {hrs}h {mins}m)'
    return f'Next reminder: tomorrow 6:00 PM (in {hrs}h {mins}m)'


def reminder_already_sent_today():
    return store.reminder_day() == dn.today_npt().isoformat()


def mark_reminder_sent_today():
    store.set_reminder_day(dn.today_npt().isoformat())


def delivery_summary(published):
    published = published or {}
    tg_ok = bool(published.get('telegram') or [])
    dc_ok = bool(published.get('discord') or [])
    if tg_ok and dc_ok:
        return 'Posted to Telegram and Discord.'
    if tg_ok:
        return 'Posted to Telegram only. Discord failed.'
    if dc_ok:
        return 'Posted to Discord only. Telegram failed.'
    return 'Post failed on both Telegram and Discord.'


class TgMenu:
    def __init__(
        self,
        http,
        token,
        chat_ids,
        discord_webhooks,
        send_discord_text_file,
        edit_discord_message=None,
        delete_discord_message=None,
    ):
        self.http = http
        self.token = token
        self.chat_ids = [str(c) for c in chat_ids]
        self.webhooks = [w for w in discord_webhooks if w]
        self.send_discord_text_file = send_discord_text_file
        self.edit_discord_message = edit_discord_message
        self.delete_discord_message = delete_discord_message
        self.api = f'https://api.telegram.org/bot{token}'

    async def api_post(self, method, data=None, json_body=None):
        if json_body is not None:
            r = await self.http.post(f'{self.api}/{method}', json=json_body, timeout=30)
        else:
            r = await self.http.post(f'{self.api}/{method}', data=data or {}, timeout=30)
        try:
            return r.json()
        except Exception:
            return {'ok': False, 'description': r.text[:200]}

    async def setup_commands(self):
        await self.api_post('setMyCommands', json_body={
            'commands': [
                {'command': 'start', 'description': 'Open menu'},
                {'command': 'post', 'description': 'Quick post (old style)'},
                {'command': 'status', 'description': 'Bot status'},
                {'command': 'cancel', 'description': 'Cancel current action'},
            ]
        })

    async def send(self, chat_id, text, reply_markup=None, track=None):
        # Telegram text limit 4096
        chunks = []
        text = text or ''
        while text:
            chunks.append(text[:4096])
            text = text[4096:]
        if not chunks:
            chunks = ['']
        ids = []
        for i, chunk in enumerate(chunks):
            payload = {'chat_id': chat_id, 'text': chunk}
            if reply_markup is not None and i == len(chunks) - 1:
                payload['reply_markup'] = json.dumps(reply_markup)
            r = await self.api_post('sendMessage', data=payload)
            if r.get('ok'):
                ids.append(r['result']['message_id'])
        if track is None:
            track = self._in_wizard(chat_id)
        if track:
            for mid in ids:
                self._track_wizard(chat_id, mid)
        print('[BOT] << Sent reply')
        return ids

    def _in_wizard(self, chat_id):
        step = str((sessions.get(str(chat_id)) or {}).get('step') or '')
        return step.startswith(('create', 'edit', 'del'))

    def _track_wizard(self, chat_id, message_id):
        if message_id is None:
            return
        sess = sessions.setdefault(str(chat_id), {'step': 'idle', 'draft': {}})
        msgs = sess.setdefault('wizard_msgs', [])
        mid = int(message_id)
        if mid not in msgs:
            msgs.append(mid)

    async def _delete_msgs(self, chat_id, message_ids):
        for mid in message_ids or []:
            try:
                await self.api_post('deleteMessage', data={
                    'chat_id': chat_id,
                    'message_id': mid,
                })
            except Exception as e:
                print(f'[BOT] deleteMessage {mid}: {e}')

    async def _cleanup_wizard(self, chat_id, extra_ids=None):
        sess = sessions.get(str(chat_id)) or {}
        ids = list(sess.get('wizard_msgs') or [])
        for mid in extra_ids or []:
            if mid is not None and int(mid) not in ids:
                ids.append(int(mid))
        await self._delete_msgs(chat_id, ids)
        if sess is not None:
            sess['wizard_msgs'] = []

    async def _reset_chat(self, chat_id, extra_ids=None):
        """Delete any tracked wizard messages (plus extras), then reset session."""
        await self._cleanup_wizard(chat_id, extra_ids=extra_ids)
        self._clear(chat_id)

    async def send_all(self, text):
        ok_any = False
        for cid in self.chat_ids:
            ids = await self.send(cid, text)
            if ids:
                ok_any = True
        return ok_any

    async def send_all_tracked(self, text):
        """Send to all chats; return telegram refs for later delete/edit."""
        tg_refs = []
        for cid in self.chat_ids:
            ids = await self.send(cid, text, track=False)
            if ids:
                tg_refs.append({'chat_id': str(cid), 'message_ids': ids})
        return tg_refs

    async def apply_expirations(self):
        """Mark newly expired assignments, tag live posts, drop stale reminder."""
        rows = store.load_manual()
        newly = dn.newly_expired_rows(rows)
        if not newly:
            return []
        store.save_manual(rows)
        titles = []
        for n in newly:
            label = notice_label(n)
            titles.append(label)
            print(f'[EXPIRE] {label}')
            try:
                await self.update_published_notice(n, allow_republish=False)
            except Exception as e:
                print(f'[EXPIRE] update live fail {label}: {e}')
        await self._cleanup_reminder_if_needed(newly)
        try:
            names = ', '.join(titles[:5])
            if len(titles) > 5:
                names += f' (+{len(titles) - 5} more)'
            await self.send_all(f'expired_ tagged ({len(newly)}):\n{names}')
        except Exception as e:
            print(f'[EXPIRE] notify fail: {e}')
        return newly

    async def _cleanup_reminder_if_needed(self, newly_expired):
        rem = store.load_reminder_posts()
        if not rem:
            return
        rem_ids = set(rem.get('notice_ids') or [])
        if not rem_ids:
            return
        hit = any((n.get('id') in rem_ids) for n in (newly_expired or []))
        if not hit:
            return
        print('[EXPIRE] deleting stored reminder messages')
        fake = {'published': {
            'telegram': rem.get('telegram') or [],
            'discord': rem.get('discord') or [],
        }}
        await self.delete_published_notice(fake)
        store.clear_reminder_posts()

    async def answer_callback(self, callback_id, text=None, alert=False):
        data = {'callback_query_id': callback_id}
        if text:
            data['text'] = text[:200]
            data['show_alert'] = bool(alert)
        await self.api_post('answerCallbackQuery', data=data)

    async def _strip_markup(self, chat_id, message_id):
        if message_id is None:
            return
        try:
            await self.api_post('editMessageReplyMarkup', data={
                'chat_id': chat_id,
                'message_id': message_id,
                'reply_markup': json.dumps({'inline_keyboard': []}),
            })
        except Exception as e:
            print(f'[BOT] strip markup: {e}')

    async def send_media(self, chat_id, notice):
        """Post notice to one chat. Returns telegram message_ids created."""
        ids = []
        full = format_notice_text(notice)[:4096]
        fid = notice.get('file_id')
        ft = notice.get('file_type')
        if not fid:
            r = await self.api_post('sendMessage', data={'chat_id': chat_id, 'text': full})
            if r.get('ok'):
                ids.append(r['result']['message_id'])
            else:
                print(f'tg send fail chat={chat_id}: {r}')
            return ids

        caption, followup = tg_media_parts(notice)
        if ft == 'photo':
            r = await self.api_post('sendPhoto', data={
                'chat_id': chat_id, 'photo': fid, 'caption': caption
            })
        else:
            r = await self.api_post('sendDocument', data={
                'chat_id': chat_id, 'document': fid, 'caption': caption
            })
        if r.get('ok'):
            ids.append(r['result']['message_id'])
        else:
            print(f'tg media fail chat={chat_id}: {r}')

        if followup:
            r2 = await self.api_post('sendMessage', data={'chat_id': chat_id, 'text': followup})
            if r2.get('ok'):
                ids.append(r2['result']['message_id'])
            else:
                print(f'tg followup fail chat={chat_id}: {r2}')
        return ids

    async def publish_notice(self, notice):
        """Post new messages to TG + Discord and save message ids on the notice."""
        tg_refs = []
        for cid in self.chat_ids:
            mids = await self.send_media(cid, notice)
            if mids:
                tg_refs.append({'chat_id': str(cid), 'message_ids': mids})

        file_bytes = None
        filename = notice.get('file_name')
        if notice.get('file_id'):
            file_bytes, dl_name = await self._download_tg_file(notice['file_id'])
            filename = filename or dl_name
        disc_refs = await self.send_discord_text_file(
            format_notice_text(notice),
            file_bytes=file_bytes,
            filename=filename,
        ) or []

        published = {'telegram': tg_refs, 'discord': disc_refs}
        if notice.get('id'):
            store.update_manual(notice['id'], published=published)
            notice['published'] = published
        return published

    async def update_published_notice(self, notice, *, allow_republish=True):
        """Edit already-posted TG + Discord messages in place."""
        published = notice.get('published') or {}
        tg_refs = published.get('telegram') or []
        disc_refs = published.get('discord') or []
        if not tg_refs and not disc_refs:
            if allow_republish:
                return await self.publish_notice(notice)
            print('[BOT] no published refs — skip (no republish)')
            return published

        full = format_notice_text(notice)[:4096]
        caption, followup = tg_media_parts(notice)
        edited_any = False

        for ref in tg_refs:
            cid = ref.get('chat_id')
            mids = ref.get('message_ids') or []
            if not cid or not mids:
                continue
            if notice.get('file_id') and len(mids) >= 1:
                r = await self.api_post('editMessageCaption', data={
                    'chat_id': cid,
                    'message_id': mids[0],
                    'caption': caption,
                })
                if r.get('ok'):
                    edited_any = True
                else:
                    print(f'tg edit caption fail: {r}')
                if len(mids) >= 2 and followup:
                    r2 = await self.api_post('editMessageText', data={
                        'chat_id': cid,
                        'message_id': mids[1],
                        'text': followup,
                    })
                    if r2.get('ok'):
                        edited_any = True
                    else:
                        print(f'tg edit text fail: {r2}')
            else:
                r = await self.api_post('editMessageText', data={
                    'chat_id': cid,
                    'message_id': mids[0],
                    'text': full,
                })
                if r.get('ok'):
                    edited_any = True
                else:
                    print(f'tg edit text fail: {r}')

        if self.edit_discord_message:
            for ref in disc_refs:
                ok = await self.edit_discord_message(
                    ref.get('webhook_url'),
                    ref.get('message_id'),
                    format_notice_text(notice),
                )
                if ok:
                    edited_any = True

        if not edited_any:
            if allow_republish:
                print('[BOT] edit failed — deleting old posts, then posting new')
                await self.delete_published_notice(notice)
                return await self.publish_notice(notice)
            print('[BOT] edit failed — not republishing')
        return published

    async def delete_published_notice(self, notice):
        """Delete previously posted TG + Discord messages. Returns True if all cleared."""
        published = notice.get('published') or {}
        ok_all = True
        for ref in published.get('telegram') or []:
            cid = ref.get('chat_id')
            for mid in ref.get('message_ids') or []:
                r = await self.api_post('deleteMessage', data={
                    'chat_id': cid,
                    'message_id': mid,
                })
                if not r.get('ok'):
                    ok_all = False
                    print(f'tg delete fail chat={cid} mid={mid}: {r}')
        if self.delete_discord_message:
            for ref in published.get('discord') or []:
                ok = await self.delete_discord_message(ref.get('webhook_url'), ref.get('message_id'))
                if not ok:
                    ok_all = False
                    print(f'discord delete fail: {ref}')
        return ok_all

    async def _download_tg_file(self, file_id):
        info = await self.api_post('getFile', data={'file_id': file_id})
        if not info.get('ok'):
            return None, None
        path = info['result']['file_path']
        url = f'https://api.telegram.org/file/bot{self.token}/{path}'
        r = await self.http.get(url, timeout=60)
        if r.status_code != 200:
            return None, None
        return r.content, path.split('/')[-1]

    def _sess(self, chat_id):
        return sessions.setdefault(str(chat_id), {'step': 'idle', 'draft': {}})

    def _clear(self, chat_id):
        sessions[str(chat_id)] = {'step': 'idle', 'draft': {}}

    async def cmd_cancel(self, chat_id, trigger_mid=None):
        waiting_for_post[str(chat_id)] = False
        await self._reset_chat(chat_id, extra_ids=[trigger_mid] if trigger_mid is not None else None)

    async def cmd_start(self, chat_id, trigger_mid=None):
        waiting_for_post[str(chat_id)] = False
        await self._reset_chat(chat_id)
        if trigger_mid is not None:
            await self._delete_msgs(chat_id, [trigger_mid])
        await self.send(
            chat_id,
            'Notices menu ready.\n'
            '/post  /status  /cancel — or use the buttons 👇',
            reply_markup=main_reply_keyboard(),
            track=False,
        )
        await self.send(
            chat_id,
            'Quick actions:',
            reply_markup=inline([
                [
                    {'text': '🆕 Create', 'callback_data': 'menu:create'},
                    {'text': '📋 List', 'callback_data': 'menu:list'},
                ],
                [
                    {'text': '✏️ Edit', 'callback_data': 'menu:edit'},
                    {'text': '🗑️ Delete', 'callback_data': 'menu:delete'},
                ],
                [
                    {'text': '📊 Status', 'callback_data': 'menu:status'},
                    {'text': '📦 Expired', 'callback_data': 'menu:expired'},
                ],
            ]),
            track=False,
        )

    async def cmd_status(self, chat_id, trigger_mid=None):
        waiting_for_post[str(chat_id)] = False
        await self._reset_chat(chat_id)
        if trigger_mid is not None:
            await self._delete_msgs(chat_id, [trigger_mid])
        await self.apply_expirations()
        rows = store.load_manual()
        active = [r for r in rows if r.get('status') != 'expired']
        assigns = [r for r in active if r.get('category') == 'assignment']
        upcoming = dn.deadlines_tomorrow(rows)
        now = dn.now_npt()
        await self.send(
            chat_id,
            '📊 Status\n'
            f'Time now: {now.strftime("%Y-%m-%d %H:%M")}\n'
            f'Active notices: {len(active)}\n'
            f'Active assignments: {len(assigns)}\n'
            f'Due tomorrow: {len(upcoming)}\n'
            f'{format_next_reminder_line(now)}\n'
            f'(no catch-up if 6pm is missed)\n'
            f'Allowed chats: {len(self.chat_ids)}\n'
            'Scrape monitor: running',
            reply_markup=inline([[{'text': '❌ Close', 'callback_data': 'menu:cancel'}]]),
            track=False,
        )

    async def cmd_post(self, chat_id, trigger_mid=None):
        await self._reset_chat(chat_id)
        if trigger_mid is not None:
            await self._delete_msgs(chat_id, [trigger_mid])
        waiting_for_post[str(chat_id)] = True
        ids = await self.send(
            chat_id,
            'Quick post mode.\nSend text, photo, or PDF now.\n(/cancel to abort)',
            reply_markup=main_reply_keyboard(),
            track=False,
        )
        sessions[str(chat_id)] = {
            'step': 'idle',
            'draft': {},
            'post_prompt_mid': ids[0] if ids else None,
        }

    async def show_list(self, chat_id):
        waiting_for_post[str(chat_id)] = False
        await self._reset_chat(chat_id)
        await self.apply_expirations()
        rows = store.load_manual()
        active = [r for r in rows if r.get('status') != 'expired']
        expired = [r for r in rows if r.get('status') == 'expired']
        active = sorted(active, key=lambda r: r.get('created_at', ''), reverse=True)[:15]
        if not active:
            await self.send(
                chat_id,
                f'No active notices.\n(expired on file: {len(expired)})',
                reply_markup=inline([
                    *([ [{'text': '📦 View expired', 'callback_data': 'menu:expired'}] ] if expired else []),
                    [{'text': '❌ Close', 'callback_data': 'menu:cancel'}],
                ]),
                track=False,
            )
            return
        lines = ['📋 Active notices', '━━━━━━━━━━━━━━━━', '']
        for r in active:
            extra = ''
            if r.get('deadline_ad'):
                extra = f" | {dn.format_deadline_short(r['deadline_ad'])}"
            lines.append(f"• {cat_emoji(r.get('category'))} {notice_label(r, 50)}{extra}")
        upcoming = dn.deadlines_tomorrow(rows)
        if upcoming:
            lines += ['', '⏳ Due tomorrow:']
            for r in upcoming:
                lines.append(f"• {notice_label(r, 50)}")
        if expired:
            lines += ['', f'(expired archived: {len(expired)} — tap Expired to view)']
        buttons = []
        if expired:
            buttons.append([{'text': '📦 View expired', 'callback_data': 'menu:expired'}])
        buttons.append([{'text': '❌ Close', 'callback_data': 'menu:cancel'}])
        await self.send(
            chat_id,
            '\n'.join(lines),
            reply_markup=inline(buttons),
            track=False,
        )

    async def show_expired(self, chat_id):
        waiting_for_post[str(chat_id)] = False
        await self._reset_chat(chat_id)
        await self.apply_expirations()
        rows = store.load_manual()
        expired = [r for r in rows if r.get('status') == 'expired']
        expired = sorted(expired, key=lambda r: r.get('deadline_ad') or r.get('created_at', ''), reverse=True)[:20]
        if not expired:
            await self.send(
                chat_id,
                'No expired notices.',
                reply_markup=inline([[{'text': '❌ Close', 'callback_data': 'menu:cancel'}]]),
                track=False,
            )
            return
        lines = ['📦 Expired notices', '━━━━━━━━━━━━━━━━', '']
        for r in expired:
            extra = f" | {dn.format_deadline_short(r['deadline_ad'])}" if r.get('deadline_ad') else ''
            lines.append(f"• {cat_emoji(r.get('category'))} {notice_label(r, 50)}{extra}")
        await self.send(chat_id, '\n'.join(lines), reply_markup=inline([
            [{'text': '❌ Close', 'callback_data': 'menu:cancel'}],
        ]), track=False)

    async def start_create(self, chat_id, trigger_mid=None):
        waiting_for_post[str(chat_id)] = False
        await self._cleanup_wizard(chat_id)
        if trigger_mid is not None:
            await self._delete_msgs(chat_id, [trigger_mid])
        sessions[str(chat_id)] = {'step': 'create_category', 'draft': {}, 'wizard_msgs': []}
        await self.send(
            chat_id,
            'Create notice — pick category:',
            reply_markup=inline([
                [{'text': '📢 General', 'callback_data': 'create:cat:general'}],
                [{'text': '📝 Assignment', 'callback_data': 'create:cat:assignment'}],
                [{'text': '🚨 Urgent', 'callback_data': 'create:cat:urgent'}],
                [{'text': '❌ Cancel', 'callback_data': 'menu:cancel'}],
            ]),
            track=True,
        )

    async def start_edit_picker(self, chat_id, trigger_mid=None):
        waiting_for_post[str(chat_id)] = False
        rows = store.recent_manual(10)
        rows = [r for r in rows if r.get('status') != 'expired']
        if not rows:
            await self._reset_chat(chat_id)
            if trigger_mid is not None:
                await self._delete_msgs(chat_id, [trigger_mid])
            await self.send(chat_id, 'Nothing to edit.', reply_markup=main_reply_keyboard(), track=False)
            return
        await self._cleanup_wizard(chat_id)
        if trigger_mid is not None:
            await self._delete_msgs(chat_id, [trigger_mid])
        buttons = []
        for r in rows:
            label = f"{notice_label(r, 50)}"[:60]
            buttons.append([{'text': label, 'callback_data': f"edit:pick:{r['id']}"}])
        buttons.append([{'text': '❌ Cancel', 'callback_data': 'menu:cancel'}])
        sessions[str(chat_id)] = {'step': 'edit_pick', 'draft': {}, 'wizard_msgs': []}
        await self.send(chat_id, 'Edit — pick a notice:', reply_markup=inline(buttons), track=True)

    async def start_delete_picker(self, chat_id, trigger_mid=None):
        waiting_for_post[str(chat_id)] = False
        rows = store.recent_manual(10)
        if not rows:
            await self._reset_chat(chat_id)
            if trigger_mid is not None:
                await self._delete_msgs(chat_id, [trigger_mid])
            await self.send(chat_id, 'Nothing to delete.', reply_markup=main_reply_keyboard(), track=False)
            return
        await self._cleanup_wizard(chat_id)
        if trigger_mid is not None:
            await self._delete_msgs(chat_id, [trigger_mid])
        buttons = []
        for r in rows:
            label = f"🗑️ {notice_label(r, 48)}"[:60]
            buttons.append([{'text': label, 'callback_data': f"del:pick:{r['id']}"}])
        buttons.append([{'text': '❌ Cancel', 'callback_data': 'menu:cancel'}])
        sessions[str(chat_id)] = {'step': 'del_pick', 'draft': {}, 'wizard_msgs': []}
        await self.send(chat_id, 'Delete — pick a notice:', reply_markup=inline(buttons), track=True)

    async def handle_update(self, update):
        if 'callback_query' in update:
            await self._on_callback(update['callback_query'])
            return
        if 'message' in update:
            await self._on_message(update['message'])

    async def _on_callback(self, cq):
        chat_id = str(cq['message']['chat']['id'])
        if not allowed(chat_id, self.chat_ids):
            await self.answer_callback(cq['id'], 'Not allowed')
            return
        data = cq.get('data') or ''
        sess = self._sess(chat_id)
        cq_mid = cq.get('message', {}).get('message_id')

        # toast answers must be the (only) answerCallbackQuery — defer those
        toast_later = (
            data == 'menu:cancel'
            or data == 'create:publish'
            or data.startswith('edit:done:')
            or data.startswith('edit:republish:')
            or data.startswith('del:yes:')
        )
        if not toast_later:
            await self.answer_callback(cq['id'])
            if data.startswith(('create:', 'edit:', 'del:')):
                await self._strip_markup(chat_id, cq_mid)

        if data == 'menu:cancel':
            was_flow = (
                self._in_wizard(chat_id)
                or bool(sess.get('wizard_msgs'))
                or waiting_for_post.get(chat_id)
            )
            await self._reset_chat(chat_id, extra_ids=[cq_mid])
            waiting_for_post[chat_id] = False
            await self.answer_callback(cq['id'], 'Cancelled' if was_flow else 'Closed')
            return
        if data == 'menu:create':
            await self.start_create(chat_id, trigger_mid=cq_mid)
            return
        if data == 'menu:list':
            await self.show_list(chat_id)
            return
        if data == 'menu:edit':
            await self.start_edit_picker(chat_id, trigger_mid=cq_mid)
            return
        if data == 'menu:delete':
            await self.start_delete_picker(chat_id, trigger_mid=cq_mid)
            return
        if data == 'menu:status':
            await self.cmd_status(chat_id)
            return
        if data == 'menu:expired':
            await self.show_expired(chat_id)
            return

        if data.startswith('create:cat:'):
            cat = data.split(':')[-1]
            sess['draft'] = {'category': cat, 'status': 'active'}
            sess['step'] = 'create_title'
            await self.send(chat_id, f'{cat_emoji(cat)} Category: {cat}\n\nSend the title:')
            return

        if data.startswith('create:file:'):
            choice = data.split(':')[-1]
            if choice == 'yes':
                sess['step'] = 'create_file_wait'
                await self.send(chat_id, 'Send a PDF or image now:')
            else:
                sess['draft'].pop('file_id', None)
                sess['draft'].pop('file_type', None)
                sess['draft'].pop('file_name', None)
                await self._after_file(chat_id, sess)
            return

        if data.startswith('create:cal:') or data.startswith('edit:cal:'):
            prefix = data.split(':')[0]
            sess['draft']['cal'] = data.split(':')[-1]
            sess['step'] = f'{prefix}_deadline_input'
            tomorrow = dn.today_npt() + timedelta(days=1)
            await self.send(
                chat_id,
                f"Type deadline as YYYY-MM-DD ({sess['draft']['cal'].upper()}).\n"
                f"Must be after today ({dn.today_npt().isoformat()}).\n"
                f"Example: {tomorrow.isoformat()}",
            )
            return

        if data == 'create:dl:confirm':
            ad = sess['draft'].get('deadline_ad')
            try:
                dn.require_future_deadline(date.fromisoformat(ad))
            except Exception as e:
                await self.send(chat_id, f'{e}\nPick calendar again:')
                sess['step'] = 'create_deadline_cal'
                await self._ask_calendar(chat_id, prefix='create')
                return
            sess['step'] = 'create_preview'
            await self._show_preview(chat_id, sess)
            return

        if data == 'create:dl:retry':
            sess['step'] = 'create_deadline_cal'
            await self._ask_calendar(chat_id, prefix='create')
            return

        if data == 'edit:dl:confirm':
            nid = sess['draft'].get('id')
            ad = sess['draft'].get('deadline_ad')
            if not nid or not ad:
                await self.send(chat_id, 'Missing data. Start Edit again.')
                self._clear(chat_id)
                return
            try:
                dn.require_future_deadline(date.fromisoformat(ad))
            except Exception as e:
                await self.send(chat_id, f'{e}\nPick calendar again:')
                sess['step'] = 'edit_deadline_cal'
                await self._ask_calendar(chat_id, prefix='edit')
                return
            store.update_manual(
                nid,
                deadline_ad=ad,
                deadline_bs=sess['draft'].get('deadline_bs'),
                status='active',
            )
            await self._after_edit(chat_id, nid, 'Deadline')
            return

        if data == 'edit:dl:retry':
            sess['step'] = 'edit_deadline_cal'
            await self._ask_calendar(chat_id, prefix='edit')
            return

        if data == 'create:publish':
            draft = sess.get('draft') or {}
            preview_mid = cq_mid
            if not draft.get('title') or not draft.get('body'):
                await self.answer_callback(cq['id'], 'Incomplete notice')
                await self._reset_chat(chat_id, extra_ids=[preview_mid])
                return
            if draft.get('category') == 'assignment' and not draft.get('deadline_ad'):
                await self.answer_callback(cq['id'], 'Needs a deadline')
                await self._reset_chat(chat_id, extra_ids=[preview_mid])
                return
            if draft.get('category') == 'assignment':
                try:
                    dn.require_future_deadline(date.fromisoformat(draft['deadline_ad']))
                except Exception as e:
                    await self.answer_callback(cq['id'], str(e)[:180])
                    await self._reset_chat(chat_id, extra_ids=[preview_mid])
                    return
            wizard_ids = list(sess.get('wizard_msgs') or [])
            if preview_mid is not None and int(preview_mid) not in wizard_ids:
                wizard_ids.append(int(preview_mid))
            notice = store.add_manual(deepcopy(draft))
            await self.answer_callback(cq['id'], f"Publishing {notice_label(notice, 40)}")
            await self._delete_msgs(chat_id, wizard_ids)
            self._clear(chat_id)
            published = await self.publish_notice(notice)
            await self.send(chat_id, delivery_summary(published), reply_markup=main_reply_keyboard(), track=False)
            return

        if data.startswith('edit:republish:'):
            nid = data.split(':')[-1]
            n = store.get_manual(nid)
            wizard_ids = list(sess.get('wizard_msgs') or [])
            if cq_mid is not None and int(cq_mid) not in wizard_ids:
                wizard_ids.append(int(cq_mid))
            if not n:
                await self.answer_callback(cq['id'], 'Not found')
                await self._delete_msgs(chat_id, wizard_ids)
                self._clear(chat_id)
                return
            await self.answer_callback(cq['id'], f'Updating {notice_label(n, 40)}')
            await self._delete_msgs(chat_id, wizard_ids)
            self._clear(chat_id)
            published = await self.update_published_notice(n)
            await self.send(chat_id, delivery_summary(published), reply_markup=main_reply_keyboard(), track=False)
            return

        if data.startswith('edit:done:'):
            await self.answer_callback(cq['id'], 'Done')
            await self._cleanup_wizard(chat_id, extra_ids=[cq_mid])
            self._clear(chat_id)
            return

        if data.startswith('edit:pick:'):
            nid = data.split(':')[-1]
            n = store.get_manual(nid)
            if not n:
                await self.send(chat_id, 'Not found.', track=True)
                return
            self._track_wizard(chat_id, cq.get('message', {}).get('message_id'))
            sess['step'] = 'edit_field'
            sess['draft'] = {'id': nid}
            buttons = [
                [{'text': 'Title', 'callback_data': 'edit:field:title'}],
                [{'text': 'Body', 'callback_data': 'edit:field:body'}],
            ]
            if n.get('category') == 'assignment':
                buttons.append([{'text': 'Deadline', 'callback_data': 'edit:field:deadline'}])
            buttons.append([{'text': '❌ Cancel', 'callback_data': 'menu:cancel'}])
            await self.send(
                chat_id,
                f'Editing — {notice_label(n)}\nWhat to change?',
                reply_markup=inline(buttons),
                track=True,
            )
            return

        if data.startswith('edit:field:'):
            field = data.split(':')[-1]
            nid = sess.get('draft', {}).get('id')
            n = store.get_manual(nid) if nid else None
            if not n:
                await self.send(chat_id, 'Not found.')
                return
            if field == 'deadline' and n.get('category') != 'assignment':
                await self.send(chat_id, 'Only assignments have deadlines.')
                return
            sess['draft']['field'] = field
            if field == 'deadline':
                sess['step'] = 'edit_deadline_cal'
                await self._ask_calendar(chat_id, prefix='edit')
            else:
                sess['step'] = f'edit_wait_{field}'
                await self.send(chat_id, f'Send new {field}:')
            return

        if data.startswith('del:pick:'):
            nid = data.split(':')[-1]
            n = store.get_manual(nid)
            preview = n.get('title', '') if n else ''
            sess['step'] = 'del_confirm'
            sess['draft'] = {'id': nid}
            self._track_wizard(chat_id, cq.get('message', {}).get('message_id'))
            await self.send(
                chat_id,
                f'Delete this notice?\n{notice_label(n) if n else nid}',
                reply_markup=inline([
                    [
                        {'text': 'Yes, delete', 'callback_data': f'del:yes:{nid}'},
                        {'text': 'No', 'callback_data': 'menu:cancel'},
                    ]
                ]),
                track=True,
            )
            return

        if data.startswith('del:yes:'):
            nid = data.split(':')[-1]
            n = store.get_manual(nid)
            label = notice_label(n) if n else nid
            wizard_ids = list(sess.get('wizard_msgs') or [])
            if cq_mid is not None and int(cq_mid) not in wizard_ids:
                wizard_ids.append(int(cq_mid))
            live_ok = True
            if n:
                live_ok = await self.delete_published_notice(n)
            ok = store.delete_manual(nid)
            await self.answer_callback(cq['id'], f'Deleted {label}'[:200] if ok else 'Not found')
            await self._delete_msgs(chat_id, wizard_ids)
            self._clear(chat_id)
            if ok and n and not live_ok:
                await self.send(
                    chat_id,
                    f'Deleted {notice_label(n)} from store, but some old Telegram/Discord posts may remain.',
                    reply_markup=main_reply_keyboard(),
                    track=False,
                )
            return

    async def _ask_calendar(self, chat_id, prefix='create'):
        await self.send(
            chat_id,
            'Deadline calendar:',
            reply_markup=inline([
                [
                    {'text': 'AD (English)', 'callback_data': f'{prefix}:cal:ad'},
                    {'text': 'BS (Nepali)', 'callback_data': f'{prefix}:cal:bs'},
                ],
                [{'text': '❌ Cancel', 'callback_data': 'menu:cancel'}],
            ]),
        )

    async def _after_file(self, chat_id, sess):
        if sess['draft'].get('category') == 'assignment':
            sess['step'] = 'create_deadline_cal'
            await self._ask_calendar(chat_id)
        else:
            sess['step'] = 'create_preview'
            await self._show_preview(chat_id, sess)

    async def _show_preview(self, chat_id, sess):
        draft = sess['draft']
        text = 'PREVIEW — tap Publish to send\n\n' + format_notice_text(draft)
        await self.send(
            chat_id,
            text,
            reply_markup=inline([
                [
                    {'text': '✅ Publish', 'callback_data': 'create:publish'},
                    {'text': '❌ Cancel', 'callback_data': 'menu:cancel'},
                ]
            ]),
        )

    async def _after_edit(self, chat_id, nid, what):
        sess = sessions.get(str(chat_id)) or {}
        wizard = list(sess.get('wizard_msgs') or [])
        n = store.get_manual(nid)
        sessions[str(chat_id)] = {
            'step': 'edit_after_save',
            'draft': {'id': nid},
            'wizard_msgs': wizard,
        }
        await self.send(
            chat_id,
            f'✅ {what} saved for {notice_label(n)}.\n'
            'Update the already-posted Telegram + Discord messages?',
            reply_markup=inline([
                [
                    {'text': '✏️ Update live posts', 'callback_data': f'edit:republish:{nid}'},
                    {'text': 'Done', 'callback_data': f'edit:done:{nid}'},
                ]
            ]),
            track=True,
        )

    async def _handle_menu_label(self, chat_id, txt, trigger_mid=None):
        """Reply-keyboard actions work anytime (not only idle)."""
        if txt == 'Create':
            await self.start_create(chat_id, trigger_mid=trigger_mid)
        elif txt == 'List':
            await self.show_list(chat_id)
            if trigger_mid is not None:
                await self._delete_msgs(chat_id, [trigger_mid])
        elif txt == 'Edit':
            await self.start_edit_picker(chat_id, trigger_mid=trigger_mid)
        elif txt == 'Delete':
            await self.start_delete_picker(chat_id, trigger_mid=trigger_mid)
        else:
            await self.cmd_status(chat_id)
            if trigger_mid is not None:
                await self._delete_msgs(chat_id, [trigger_mid])

    async def _on_message(self, msg):
        chat_id = str(msg['chat']['id'])
        if not allowed(chat_id, self.chat_ids):
            print(f'[BOT] ignore chat {chat_id}')
            return

        txt = (msg.get('text') or '').strip()
        sess = self._sess(chat_id)
        step = sess.get('step', 'idle')

        if txt.startswith('/start'):
            await self.cmd_start(chat_id, trigger_mid=msg.get('message_id'))
            return
        if txt.startswith('/status'):
            await self.cmd_status(chat_id, trigger_mid=msg.get('message_id'))
            return
        if txt.startswith('/cancel'):
            await self.cmd_cancel(chat_id, trigger_mid=msg.get('message_id'))
            return
        if txt == '/post' or txt.startswith('/post'):
            if txt.startswith('/post ') and len(txt) > 6:
                waiting_for_post[chat_id] = False
                content = txt[6:].strip()
                if content:
                    await self._delete_msgs(chat_id, [msg.get('message_id')])
                    published = await self._post_quick_text(content)
                    await self.send(chat_id, delivery_summary(published), reply_markup=main_reply_keyboard(), track=False)
                return
            await self.cmd_post(chat_id, trigger_mid=msg.get('message_id'))
            return

        # bottom keyboard always wins (restart flow)
        if txt in ('Create', 'List', 'Edit', 'Delete', 'Status'):
            await self._handle_menu_label(chat_id, txt, trigger_mid=msg.get('message_id'))
            return

        if waiting_for_post.get(chat_id):
            await self._quick_post(chat_id, msg)
            return

        # during Create/Edit/Delete wizard, track user replies so we can delete them at the end
        if self._in_wizard(chat_id) and msg.get('message_id') is not None:
            self._track_wizard(chat_id, msg['message_id'])

        if step == 'create_title' and txt:
            sess['draft']['title'] = txt
            sess['step'] = 'create_body'
            await self.send(chat_id, 'Send body text:')
            return

        if step == 'create_body' and txt:
            sess['draft']['body'] = txt
            sess['step'] = 'create_file_ask'
            await self.send(
                chat_id,
                'Attach a PDF or image?',
                reply_markup=inline([
                    [
                        {'text': 'Yes', 'callback_data': 'create:file:yes'},
                        {'text': 'No / skip', 'callback_data': 'create:file:no'},
                    ],
                    [{'text': '❌ Cancel', 'callback_data': 'menu:cancel'}],
                ]),
            )
            return

        if step == 'create_file_wait':
            if 'document' in msg:
                doc = msg['document']
                sess['draft']['file_id'] = doc['file_id']
                sess['draft']['file_type'] = 'document'
                sess['draft']['file_name'] = doc.get('file_name') or 'file.bin'
                await self._after_file(chat_id, sess)
            elif 'photo' in msg:
                sess['draft']['file_id'] = msg['photo'][-1]['file_id']
                sess['draft']['file_type'] = 'photo'
                sess['draft']['file_name'] = 'photo.jpg'
                await self._after_file(chat_id, sess)
            else:
                await self.send(
                    chat_id,
                    'Please send a PDF or image.',
                    reply_markup=inline([[{'text': '❌ Cancel', 'callback_data': 'menu:cancel'}]]),
                )
            return

        if step == 'create_deadline_input' and txt:
            await self._handle_deadline_input(chat_id, sess, txt, mode='create')
            return

        if step == 'edit_wait_title' and txt:
            nid = sess['draft']['id']
            store.update_manual(nid, title=txt)
            await self._after_edit(chat_id, nid, 'Title')
            return

        if step == 'edit_wait_body' and txt:
            nid = sess['draft']['id']
            store.update_manual(nid, body=txt)
            await self._after_edit(chat_id, nid, 'Body')
            return

        if step == 'edit_deadline_input' and txt:
            await self._handle_deadline_input(chat_id, sess, txt, mode='edit')
            return

        if step == 'idle' and ('document' in msg or 'photo' in msg):
            await self.send(chat_id, 'Use /post for quick dump, or Create for a full notice.',
                            reply_markup=main_reply_keyboard())

    async def _handle_deadline_input(self, chat_id, sess, txt, mode):
        cal = sess['draft'].get('cal', 'ad')
        try:
            if cal == 'bs':
                ad = dn.bs_to_ad(txt)
            else:
                ad = dn.ad_from_input(txt)
            dn.require_future_deadline(ad)
            bs_str = dn.ad_to_bs_str(ad)
        except Exception as e:
            await self.send(chat_id, f'Invalid date: {e}\nTry again (YYYY-MM-DD):')
            return

        # soft sanity: year range
        if ad.year < 2000 or ad.year > 2100:
            await self.send(chat_id, 'That year looks wrong. Try again (YYYY-MM-DD):')
            return

        sess['draft']['deadline_ad'] = ad.isoformat()
        sess['draft']['deadline_bs'] = bs_str
        pair = dn.format_deadline_pair(ad)

        prefix = 'create' if mode == 'create' else 'edit'
        sess['step'] = f'{mode}_deadline_confirm'
        await self.send(
            chat_id,
            f'Confirm deadline?\n\n{pair}',
            reply_markup=inline([
                [
                    {'text': '✅ Confirm', 'callback_data': f'{prefix}:dl:confirm'},
                    {'text': '🔄 Re-enter', 'callback_data': f'{prefix}:dl:retry'},
                ],
                [{'text': '❌ Cancel', 'callback_data': 'menu:cancel'}],
            ]),
        )

    async def _post_quick_text(self, txt):
        tg_refs = []
        for cid in self.chat_ids:
            ids = await self.send(cid, f'🎤 info_s\n\n{txt}', track=False)
            if ids:
                tg_refs.append({'chat_id': str(cid), 'message_ids': ids})
        disc_refs = await self.send_discord_text_file(f'info_s\n\n{txt}') or []
        return {'telegram': tg_refs, 'discord': disc_refs}

    async def _post_quick_media(self, file_id, kind, caption, filename=None):
        tg_refs = []
        cap = f'📄 {caption}'[:1024]
        for cid in self.chat_ids:
            if kind == 'photo':
                r = await self.api_post('sendPhoto', data={
                    'chat_id': cid, 'photo': file_id, 'caption': cap
                })
            else:
                r = await self.api_post('sendDocument', data={
                    'chat_id': cid, 'document': file_id, 'caption': cap
                })
            if r.get('ok'):
                tg_refs.append({'chat_id': str(cid), 'message_ids': [r['result']['message_id']]})
            else:
                print(f'quick post tg fail chat={cid}: {r}')
        file_bytes, dl_name = await self._download_tg_file(file_id)
        disc_refs = await self.send_discord_text_file(
            f'📄 {caption}',
            file_bytes=file_bytes,
            filename=filename or dl_name,
        ) or []
        return {'telegram': tg_refs, 'discord': disc_refs}

    async def _quick_post(self, chat_id, msg):
        waiting_for_post[chat_id] = False
        sess = sessions.get(str(chat_id)) or {}
        prompt_mid = sess.get('post_prompt_mid')
        extra = [msg.get('message_id'), prompt_mid]
        try:
            if 'document' in msg or 'photo' in msg:
                if 'document' in msg:
                    file_id = msg['document']['file_id']
                    caption = msg.get('caption', 'info_s')
                    kind = 'document'
                    filename = msg['document'].get('file_name')
                else:
                    file_id = msg['photo'][-1]['file_id']
                    caption = msg.get('caption', 'info_s')
                    kind = 'photo'
                    filename = 'photo.jpg'
                published = await self._post_quick_media(file_id, kind, caption, filename=filename)
                await self._delete_msgs(chat_id, extra)
                self._clear(chat_id)
                await self.send(chat_id, delivery_summary(published), reply_markup=main_reply_keyboard(), track=False)
                return

            txt = (msg.get('text') or '').strip()
            if txt:
                published = await self._post_quick_text(txt)
                await self._delete_msgs(chat_id, extra)
                self._clear(chat_id)
                await self.send(chat_id, delivery_summary(published), reply_markup=main_reply_keyboard(), track=False)
        except Exception as e:
            print(f'quick post error: {e}')
            await self._delete_msgs(chat_id, extra)
            self._clear(chat_id)
            await self.send(chat_id, 'Failed to post', reply_markup=main_reply_keyboard(), track=False)
