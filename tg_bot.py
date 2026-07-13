# Telegram menu / wizard for manual notices
import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import store
import dates_npt as dn

# per-chat wizard state: { chat_id: {step, draft, ...} }
sessions = {}
waiting_for_post = {}  # chat_id -> True after /post
REMINDER_FLAG = Path('reminder_sent_day.txt')


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


def format_notice_text(n):
    """Full notice text for preview / Discord / follow-up message."""
    cat = n.get('category', 'general')
    title = (n.get('title') or '').strip() or '(no title)'
    body = (n.get('body') or '').strip() or '(no body)'

    if cat == 'urgent':
        lines = [
            '🚨🚨 URGENT NOTICE 🚨🚨',
            '━━━━━━━━━━━━━━━━',
            title,
            '',
            body,
            '━━━━━━━━━━━━━━━━',
        ]
    elif cat == 'assignment':
        lines = [
            '📝 ASSIGNMENT',
            '━━━━━━━━━━━━━━━━',
            title,
            '',
            body,
        ]
    else:
        lines = [
            f'{cat_emoji(cat)} GENERAL NOTICE',
            '━━━━━━━━━━━━━━━━',
            title,
            '',
            body,
        ]

    if cat == 'assignment' and n.get('deadline_ad'):
        lines += ['', f"⏰ Deadline: {dn.format_deadline_pair(date.fromisoformat(n['deadline_ad']))}"]
        if n.get('status') == 'expired':
            lines.append('⚠️ Status: EXPIRED')
    if n.get('file_name'):
        lines += ['', f"📎 {n.get('file_name')}"]
    elif n.get('file_type'):
        lines += ['', f"📎 Attachment: {n.get('file_type')}"]
    return '\n'.join(lines)


def format_caption(n):
    """Short caption for photo/document (Telegram limit 1024)."""
    cat = n.get('category', 'general')
    title = (n.get('title') or '').strip() or '(no title)'
    lines = [f'{cat_emoji(cat)} {cat.upper()}', title]
    if cat == 'assignment' and n.get('deadline_ad'):
        lines.append(f"⏰ {dn.format_deadline_pair(date.fromisoformat(n['deadline_ad']))}")
    lines.append('(see next message for details)' if (n.get('body') or '').strip() else '')
    text = '\n'.join([x for x in lines if x])
    return text[:1024]


def format_reminder_bundle(items):
    lines = [
        '⏰ DEADLINE REMINDER',
        '━━━━━━━━━━━━━━━━',
        'Due TOMORROW (Nepal time)',
        '',
    ]
    for i, n in enumerate(items, 1):
        title = n.get('title', 'Untitled')
        lines.append(f'{i}. 📝 {title}')
        lines.append(f"   ⏰ {dn.format_deadline_pair(date.fromisoformat(n['deadline_ad']))}")
        lines.append('')
    lines.append('━━━━━━━━━━━━━━━━')
    lines.append('Finish it before midnight 💪')
    return '\n'.join(lines)


def format_next_reminder_line(now=None):
    now = now or dn.now_npt()
    secs = dn.seconds_until_next_6pm_npt(now)
    hrs, rem = divmod(secs, 3600)
    mins = rem // 60
    if now.hour < 18:
        return f'Next reminder: today 6:00 PM NPT (in {hrs}h {mins}m)'
    return f'Next reminder: tomorrow 6:00 PM NPT (in {hrs}h {mins}m)'


def reminder_already_sent_today():
    day = dn.today_npt().isoformat()
    try:
        return REMINDER_FLAG.read_text().strip() == day
    except Exception:
        return False


def mark_reminder_sent_today():
    REMINDER_FLAG.write_text(dn.today_npt().isoformat() + '\n', encoding='utf-8')


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

    async def send(self, chat_id, text, reply_markup=None):
        # Telegram text limit 4096
        chunks = []
        text = text or ''
        while text:
            chunks.append(text[:4096])
            text = text[4096:]
        if not chunks:
            chunks = ['']
        for i, chunk in enumerate(chunks):
            payload = {'chat_id': chat_id, 'text': chunk}
            if reply_markup is not None and i == len(chunks) - 1:
                payload['reply_markup'] = json.dumps(reply_markup)
            await self.api_post('sendMessage', data=payload)
        print('[BOT] << Sent reply')

    async def send_all(self, text):
        for cid in self.chat_ids:
            await self.send(cid, text)

    async def answer_callback(self, callback_id, text=None):
        data = {'callback_query_id': callback_id}
        if text:
            data['text'] = text[:200]
        await self.api_post('answerCallbackQuery', data=data)

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
            return ids

        caption = format_caption(notice)
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

        body = (notice.get('body') or '').strip()
        if body:
            r2 = await self.api_post('sendMessage', data={'chat_id': chat_id, 'text': full})
            if r2.get('ok'):
                ids.append(r2['result']['message_id'])
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

    async def update_published_notice(self, notice):
        """Edit already-posted TG + Discord messages in place."""
        published = notice.get('published') or {}
        tg_refs = published.get('telegram') or []
        disc_refs = published.get('discord') or []
        if not tg_refs and not disc_refs:
            return await self.publish_notice(notice)

        full = format_notice_text(notice)[:4096]
        caption = format_caption(notice)
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
                if len(mids) >= 2:
                    r2 = await self.api_post('editMessageText', data={
                        'chat_id': cid,
                        'message_id': mids[1],
                        'text': full,
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
            print('[BOT] edit failed — posting as new messages')
            return await self.publish_notice(notice)
        return published

    async def delete_published_notice(self, notice):
        """Delete previously posted TG + Discord messages."""
        published = notice.get('published') or {}
        for ref in published.get('telegram') or []:
            cid = ref.get('chat_id')
            for mid in ref.get('message_ids') or []:
                r = await self.api_post('deleteMessage', data={
                    'chat_id': cid,
                    'message_id': mid,
                })
                if not r.get('ok'):
                    print(f'tg delete fail chat={cid} mid={mid}: {r}')
        if self.delete_discord_message:
            for ref in published.get('discord') or []:
                ok = await self.delete_discord_message(ref.get('webhook_url'), ref.get('message_id'))
                if not ok:
                    print(f'discord delete fail: {ref}')

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

    async def cmd_cancel(self, chat_id):
        waiting_for_post[str(chat_id)] = False
        self._clear(chat_id)
        await self.send(chat_id, 'Cancelled.', reply_markup=main_reply_keyboard())

    async def cmd_start(self, chat_id):
        waiting_for_post[str(chat_id)] = False
        self._clear(chat_id)
        await self.send(
            chat_id,
            'Notices menu ready.\n\n'
            'Commands: /start  /post  /status  /cancel\n'
            'Or use the buttons below 👇',
            reply_markup=main_reply_keyboard(),
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
        )

    async def cmd_status(self, chat_id):
        waiting_for_post[str(chat_id)] = False
        self._clear(chat_id)
        rows = store.load_manual()
        if dn.mark_expired_rows(rows):
            store.save_manual(rows)
        active = [r for r in rows if r.get('status') != 'expired']
        assigns = [r for r in active if r.get('category') == 'assignment']
        upcoming = dn.deadlines_tomorrow(rows)
        now = dn.now_npt()
        await self.send(
            chat_id,
            '📊 Status\n'
            f'Nepal time now: {now.strftime("%Y-%m-%d %H:%M")}\n'
            f'Active notices: {len(active)}\n'
            f'Active assignments: {len(assigns)}\n'
            f'Due tomorrow: {len(upcoming)}\n'
            f'{format_next_reminder_line(now)}\n'
            f'(no catch-up if 6pm is missed)\n'
            f'Allowed chats: {len(self.chat_ids)}\n'
            'Scrape monitor: running',
            reply_markup=main_reply_keyboard(),
        )

    async def cmd_post(self, chat_id):
        self._clear(chat_id)
        waiting_for_post[str(chat_id)] = True
        await self.send(
            chat_id,
            'Quick post mode.\nSend text, photo, or PDF now.\n(/cancel to abort)',
            reply_markup=main_reply_keyboard(),
        )

    async def show_list(self, chat_id):
        waiting_for_post[str(chat_id)] = False
        self._clear(chat_id)
        rows = store.load_manual()
        if dn.mark_expired_rows(rows):
            store.save_manual(rows)
        active = [r for r in rows if r.get('status') != 'expired']
        expired = [r for r in rows if r.get('status') == 'expired']
        active = sorted(active, key=lambda r: r.get('created_at', ''), reverse=True)[:15]
        if not active:
            await self.send(
                chat_id,
                f'No active notices.\n(expired on file: {len(expired)})',
                reply_markup=inline([
                    [{'text': '📦 View expired', 'callback_data': 'menu:expired'}],
                ]) if expired else main_reply_keyboard(),
            )
            return
        lines = ['📋 Active notices', '━━━━━━━━━━━━━━━━', '']
        for r in active:
            extra = ''
            if r.get('deadline_ad'):
                extra = f" | ⏰ {r['deadline_ad']}"
            lines.append(f"• {r['id']} {cat_emoji(r.get('category'))} {r.get('title', '')}{extra}")
        upcoming = dn.deadlines_tomorrow(rows)
        if upcoming:
            lines += ['', '⏳ Due tomorrow:']
            for r in upcoming:
                lines.append(f"• {r.get('title')} ({r.get('deadline_ad')})")
        if expired:
            lines += ['', f'(expired archived: {len(expired)} — tap Expired to view)']
        await self.send(
            chat_id,
            '\n'.join(lines),
            reply_markup=inline([
                [{'text': '📦 View expired', 'callback_data': 'menu:expired'}],
                [{'text': '❌ Close', 'callback_data': 'menu:cancel'}],
            ]) if expired else main_reply_keyboard(),
        )

    async def show_expired(self, chat_id):
        waiting_for_post[str(chat_id)] = False
        self._clear(chat_id)
        rows = store.load_manual()
        if dn.mark_expired_rows(rows):
            store.save_manual(rows)
        expired = [r for r in rows if r.get('status') == 'expired']
        expired = sorted(expired, key=lambda r: r.get('deadline_ad') or r.get('created_at', ''), reverse=True)[:20]
        if not expired:
            await self.send(chat_id, 'No expired notices.', reply_markup=main_reply_keyboard())
            return
        lines = ['📦 Expired notices', '━━━━━━━━━━━━━━━━', '']
        for r in expired:
            extra = f" | ⏰ {r['deadline_ad']}" if r.get('deadline_ad') else ''
            lines.append(f"• {r['id']} {cat_emoji(r.get('category'))} {r.get('title', '')}{extra}")
        await self.send(chat_id, '\n'.join(lines), reply_markup=main_reply_keyboard())

    async def start_create(self, chat_id):
        waiting_for_post[str(chat_id)] = False
        sessions[str(chat_id)] = {'step': 'create_category', 'draft': {}}
        await self.send(
            chat_id,
            'Create notice — pick category:',
            reply_markup=inline([
                [{'text': '📢 General', 'callback_data': 'create:cat:general'}],
                [{'text': '📝 Assignment', 'callback_data': 'create:cat:assignment'}],
                [{'text': '🚨 Urgent', 'callback_data': 'create:cat:urgent'}],
                [{'text': '❌ Cancel', 'callback_data': 'menu:cancel'}],
            ]),
        )

    async def start_edit_picker(self, chat_id):
        waiting_for_post[str(chat_id)] = False
        rows = store.recent_manual(10)
        rows = [r for r in rows if r.get('status') != 'expired']
        if not rows:
            await self.send(chat_id, 'Nothing to edit.', reply_markup=main_reply_keyboard())
            return
        buttons = []
        for r in rows:
            label = f"{r['id']} {r.get('title', '')}"[:60]
            buttons.append([{'text': label, 'callback_data': f"edit:pick:{r['id']}"}])
        buttons.append([{'text': '❌ Cancel', 'callback_data': 'menu:cancel'}])
        sessions[str(chat_id)] = {'step': 'edit_pick', 'draft': {}}
        await self.send(chat_id, 'Edit — pick a notice:', reply_markup=inline(buttons))

    async def start_delete_picker(self, chat_id):
        waiting_for_post[str(chat_id)] = False
        rows = store.recent_manual(10)
        if not rows:
            await self.send(chat_id, 'Nothing to delete.', reply_markup=main_reply_keyboard())
            return
        buttons = []
        for r in rows:
            label = f"🗑️ {r['id']} {r.get('title', '')}"[:60]
            buttons.append([{'text': label, 'callback_data': f"del:pick:{r['id']}"}])
        buttons.append([{'text': '❌ Cancel', 'callback_data': 'menu:cancel'}])
        await self.send(chat_id, 'Delete — pick a notice:', reply_markup=inline(buttons))

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
        await self.answer_callback(cq['id'])
        sess = self._sess(chat_id)

        if data == 'menu:cancel':
            self._clear(chat_id)
            waiting_for_post[chat_id] = False
            await self.send(chat_id, 'Cancelled.', reply_markup=main_reply_keyboard())
            return
        if data == 'menu:create':
            await self.start_create(chat_id)
            return
        if data == 'menu:list':
            await self.show_list(chat_id)
            return
        if data == 'menu:edit':
            await self.start_edit_picker(chat_id)
            return
        if data == 'menu:delete':
            await self.start_delete_picker(chat_id)
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
            await self.send(
                chat_id,
                f"Type deadline as YYYY-MM-DD ({sess['draft']['cal'].upper()}):\n"
                f"Example: 2026-07-20",
            )
            return

        if data == 'create:dl:confirm':
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
            if not draft.get('title') or not draft.get('body'):
                await self.send(chat_id, 'Incomplete notice. Start Create again.')
                self._clear(chat_id)
                return
            if draft.get('category') == 'assignment' and not draft.get('deadline_ad'):
                await self.send(chat_id, 'Assignment needs a deadline. Start Create again.')
                self._clear(chat_id)
                return
            notice = store.add_manual(deepcopy(draft))
            self._clear(chat_id)
            await self.publish_notice(notice)
            await self.send(chat_id, f"✅ Published {notice['id']}",
                            reply_markup=main_reply_keyboard())
            return

        if data.startswith('edit:republish:'):
            nid = data.split(':')[-1]
            n = store.get_manual(nid)
            self._clear(chat_id)
            if not n:
                await self.send(chat_id, 'Not found.', reply_markup=main_reply_keyboard())
                return
            await self.update_published_notice(n)
            await self.send(
                chat_id,
                f'✅ Updated live messages for {nid} (Telegram + Discord)',
                reply_markup=main_reply_keyboard(),
            )
            return

        if data.startswith('edit:pick:'):
            nid = data.split(':')[-1]
            n = store.get_manual(nid)
            if not n:
                await self.send(chat_id, 'Not found.')
                return
            sess['step'] = 'edit_field'
            sess['draft'] = {'id': nid}
            buttons = [
                [{'text': 'Title', 'callback_data': 'edit:field:title'}],
                [{'text': 'Body', 'callback_data': 'edit:field:body'}],
            ]
            if n.get('category') == 'assignment':
                buttons.append([{'text': 'Deadline', 'callback_data': 'edit:field:deadline'}])
            buttons.append([{'text': '❌ Cancel', 'callback_data': 'menu:cancel'}])
            await self.send(chat_id, f'Editing {nid} — what to change?', reply_markup=inline(buttons))
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
            await self.send(
                chat_id,
                f'Delete {nid}?\n{preview}',
                reply_markup=inline([
                    [
                        {'text': 'Yes, delete', 'callback_data': f'del:yes:{nid}'},
                        {'text': 'No', 'callback_data': 'menu:cancel'},
                    ]
                ]),
            )
            return

        if data.startswith('del:yes:'):
            nid = data.split(':')[-1]
            n = store.get_manual(nid)
            if n:
                await self.delete_published_notice(n)
            ok = store.delete_manual(nid)
            self._clear(chat_id)
            await self.send(
                chat_id,
                f'🗑️ Deleted {nid} (JSON + live TG/Discord messages)' if ok else 'Not found.',
                reply_markup=main_reply_keyboard(),
            )

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
        warn = ''
        if draft.get('deadline_ad'):
            try:
                if date.fromisoformat(draft['deadline_ad']) < dn.today_npt():
                    warn = '\n⚠️ Warning: deadline is already in the past.\n'
            except Exception:
                pass
        text = '📣 PREVIEW — tap Publish to send' + warn + '\n\n' + format_notice_text(draft)
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
        self._clear(chat_id)
        await self.send(
            chat_id,
            f'✅ {what} saved for {nid}.\n'
            'Update the already-posted Telegram + Discord messages?',
            reply_markup=inline([
                [
                    {'text': '✏️ Update live posts', 'callback_data': f'edit:republish:{nid}'},
                    {'text': 'Done', 'callback_data': 'menu:cancel'},
                ]
            ]),
        )

    async def _handle_menu_label(self, chat_id, txt):
        """Reply-keyboard actions work anytime (not only idle)."""
        if txt == 'Create':
            await self.start_create(chat_id)
        elif txt == 'List':
            await self.show_list(chat_id)
        elif txt == 'Edit':
            await self.start_edit_picker(chat_id)
        elif txt == 'Delete':
            await self.start_delete_picker(chat_id)
        else:
            await self.cmd_status(chat_id)

    async def _on_message(self, msg):
        chat_id = str(msg['chat']['id'])
        if not allowed(chat_id, self.chat_ids):
            print(f'[BOT] ignore chat {chat_id}')
            return

        txt = (msg.get('text') or '').strip()
        sess = self._sess(chat_id)
        step = sess.get('step', 'idle')

        if txt.startswith('/start'):
            await self.cmd_start(chat_id)
            return
        if txt.startswith('/status'):
            await self.cmd_status(chat_id)
            return
        if txt.startswith('/cancel'):
            await self.cmd_cancel(chat_id)
            return
        if txt == '/post' or txt.startswith('/post'):
            if txt.startswith('/post ') and len(txt) > 6:
                waiting_for_post[chat_id] = False
                content = txt[6:].strip()
                if content:
                    await self.send_discord_text_file(f'info_s\n\n{content}')
                    for cid in self.chat_ids:
                        await self.send(cid, f'🎤 info_s\n\n{content}')
                    await self.send(chat_id, 'Posted!', reply_markup=main_reply_keyboard())
                return
            await self.cmd_post(chat_id)
            return

        # bottom keyboard always wins (restart flow)
        if txt in ('Create', 'List', 'Edit', 'Delete', 'Status'):
            await self._handle_menu_label(chat_id, txt)
            return

        if waiting_for_post.get(chat_id):
            await self._quick_post(chat_id, msg)
            return

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
        warn = ''
        if ad < dn.today_npt():
            warn = '\n⚠️ This date is already past.'
        elif ad == dn.today_npt():
            warn = '\nℹ️ Deadline is today (reminder already passed if after 6pm).'

        prefix = 'create' if mode == 'create' else 'edit'
        sess['step'] = f'{mode}_deadline_confirm'
        await self.send(
            chat_id,
            f'Confirm deadline?{warn}\n\n{pair}',
            reply_markup=inline([
                [
                    {'text': '✅ Confirm', 'callback_data': f'{prefix}:dl:confirm'},
                    {'text': '🔄 Re-enter', 'callback_data': f'{prefix}:dl:retry'},
                ],
                [{'text': '❌ Cancel', 'callback_data': 'menu:cancel'}],
            ]),
        )

    async def _quick_post(self, chat_id, msg):
        waiting_for_post[chat_id] = False
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
                for cid in self.chat_ids:
                    if kind == 'photo':
                        await self.api_post('sendPhoto', data={
                            'chat_id': cid, 'photo': file_id, 'caption': f'📄 {caption}'[:1024]
                        })
                    else:
                        await self.api_post('sendDocument', data={
                            'chat_id': cid, 'document': file_id, 'caption': f'📄 {caption}'[:1024]
                        })
                file_bytes, dl_name = await self._download_tg_file(file_id)
                await self.send_discord_text_file(
                    f'📄 {caption}',
                    file_bytes=file_bytes,
                    filename=filename or dl_name,
                )
                await self.send(chat_id, 'Posted!', reply_markup=main_reply_keyboard())
                return

            txt = (msg.get('text') or '').strip()
            if txt:
                await self.send_discord_text_file(f'info_s\n\n{txt}')
                for cid in self.chat_ids:
                    await self.send(cid, f'🎤 info_s\n\n{txt}')
                await self.send(chat_id, 'Posted!', reply_markup=main_reply_keyboard())
        except Exception as e:
            print(f'quick post error: {e}')
            await self.send(chat_id, 'Failed to post', reply_markup=main_reply_keyboard())
