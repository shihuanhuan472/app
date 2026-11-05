"""Durable 100-page import batches. SQLite checkpoints are shared by API workers."""
import asyncio
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from services.confluence_client import ConfluenceClient, ConfluenceError
from services.confluence_import_service import stage_page, discard_staged
from services.confluence_refresh import blocking_warnings

BATCH_SIZE = 100
LEASE_SECONDS = 300
_tasks = set()


@contextmanager
def connection():
    path = Path('.runtime/confluence-jobs.sqlite3')
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    try:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, user_id INTEGER, status TEXT, data TEXT, progress TEXT, owner TEXT, lease REAL)')
        with db:
            yield db
    finally:
        db.close()


def create_job(user_id, root_url, include_attachments):
    client = ConfluenceClient()
    try:
        root_id = client.page_id(root_url)  # Validation only; no network request.
    finally:
        client.close()
    state = dict(root_id=root_id, root_url=root_url, include_attachments=include_attachments,
                 pending=[{'id': root_id, 'title': root_id}], seen=[], titles={}, staged=[],
                 processed=0, batch_processed=0, batches=0, files=[], warnings=[], notices=[], failures=[],
                 skipped_files=0, replaced_documents=0, page_count=0, error='', started_at=time.time())
    with connection() as db:
        # One active collector per user avoids duplicate refreshes of the same documents.
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute("SELECT id FROM jobs WHERE user_id=? AND status IN ('running','paused') ORDER BY rowid DESC LIMIT 1", (user_id,)).fetchone()
        if existing:
            return existing['id']
        token = str(uuid.uuid4())
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',
                   (token, user_id, 'running', json.dumps(state, ensure_ascii=False), '{}', '', 0))
    return token


def read_job(user_id, token=None):
    with connection() as db:
        if token:
            row = db.execute('SELECT * FROM jobs WHERE user_id=? AND id=?', (user_id, str(token))).fetchone()
        else:
            row = db.execute('SELECT * FROM jobs WHERE user_id=? ORDER BY rowid DESC LIMIT 1', (user_id,)).fetchone()
    if not row:
        return None
    state = json.loads(row['data'])
    return dict(id=row['id'], status=row['status'], **state, progress=json.loads(row['progress']))


def save(token, owner, state, status='running'):
    with connection() as db:
        changed = db.execute('UPDATE jobs SET data=?,status=?,lease=? WHERE id=? AND owner=?',
                             (json.dumps(state, ensure_ascii=False), status, time.time()+LEASE_SECONDS, token, owner)).rowcount
        if not changed:
            raise RuntimeError('采集任务执行权已转移，请刷新查看进度')


def progress(token, owner, **values):
    with connection() as db:
        row = db.execute('SELECT progress FROM jobs WHERE id=? AND owner=?', (token, owner)).fetchone()
        if not row:
            raise RuntimeError('采集任务执行权已转移')
        current = json.loads(row['progress'])
        current.update(values)
        db.execute('UPDATE jobs SET progress=?,lease=? WHERE id=? AND owner=?',
                   (json.dumps(current, ensure_ascii=False), time.time()+LEASE_SECONDS, token, owner))


def fatal(exc):
    return any(code in str(exc) for code in ('403', '429', '401', '网络请求失败', 'SSO'))


def collect_batch(state, report, checkpoint, client_factory=ConfluenceClient):
    """Process at most 100 queue entries, discovering descendants incrementally."""
    client = client_factory()
    client.progress_callback = report
    seen = set(state['seen'])
    count = state['batch_processed']
    try:
        while state['pending']:
            node = state['pending'][0]
            key = str(node['id'])
            if key in seen:
                state['pending'].pop(0)
                continue
            report(phase='采集页面及附件', page_id=key, title=node.get('title', key),
                   batch=state['batches']+1, batch_completed=count, completed=state['processed'], pending=len(state['pending']))
            body_failed = False
            try:
                try:
                    page = client.page(key)
                except ConfluenceError as exc:
                    if fatal(exc):
                        raise
                    body_failed = True
                    page = node
                    state['failures'].append(dict(page_id=key, title=node.get('title', key), error=str(exc)))
                children = list(client.children(key))
                state['titles'][key] = page.get('title', key)
                queued = {str(item['id']) for item in state['pending']}
                state['pending'].extend(child for child in children if str(child['id']) not in seen and str(child['id']) not in queued)
                if not body_failed:
                    for ancestor in page.get('ancestors', []):
                        ancestor['title'] = state['titles'].get(str(ancestor['id']), ancestor['title'])
                    staged = stage_page(client, state['root_id'], key, state['include_attachments'],
                                        page_data=page, child_pages=children)
                    problems = blocking_warnings(staged)
                    if problems:
                        discard_staged(staged['files'], staged['image_paths'])
                        state['failures'].append(dict(page_id=key, title=page['title'], error='；'.join(problems)))
                    else:
                        state['staged'].append(staged)
            except (ConfluenceError, ValueError) as exc:
                if fatal(exc):
                    state['error'] = str(exc)
                    checkpoint()
                    return False  # Leave the current entry queued for explicit resume.
                state['failures'].append(dict(page_id=key, title=node.get('title', key), error=str(exc)))
            state['pending'].pop(0)
            seen.add(key)
            state['seen'].append(key)
            state['processed'] += 1
            count += 1
            state['batch_processed'] = count
            checkpoint()
            if count >= BATCH_SIZE:
                break
        return True
    finally:
        client.close()


async def run_job(token, user_id, owner):
    from database import AsyncSessionLocal
    from routers.confluence import persist_branch
    state = read_job(user_id, token)
    for field in ('id', 'status', 'progress'):
        state.pop(field, None)
    def report(**values):
        progress(token, owner, **values)
    async def heartbeat():
        while True:
            await asyncio.sleep(30)
            await asyncio.to_thread(progress, token, owner)
    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        while state['pending'] or state['staged']:
            # A full batch checkpoint may survive a process restart before registration.
            full = state['batch_processed'] >= BATCH_SIZE
            proceed = True if full else await asyncio.to_thread(collect_batch, state, report, lambda: save(token, owner, state))
            report(phase='本批整理入库', batch=state['batches']+1, completed=state['processed'], url='')
            if state['staged']:
                async with AsyncSessionLocal() as db:
                    try:
                        response = await persist_branch(db, user_id, {'pages': state['staged']}, report)
                    except Exception:
                        # Registration may clean staged files on rollback; resample these pages on resume.
                        for item in state['staged']:
                            key = str(item['source']['page_id'])
                            state['pending'].insert(0, {'id': key, 'title': key})
                            if key in state['seen']:
                                state['seen'].remove(key)
                        state['staged'] = []
                        raise
                result = response.data
                for field in ('files', 'warnings', 'notices'):
                    state[field].extend(result.get(field, []))
                for field in ('skipped_files', 'replaced_documents', 'page_count'):
                    state[field] += result.get(field, 0)
                state['staged'] = []
            state['batches'] += 1
            state['batch_processed'] = 0
            save(token, owner, state, 'running' if proceed else 'paused')
            if not proceed:
                return
        save(token, owner, state, 'finished')
    except BaseException as exc:
        state['error'] = str(getattr(exc, 'detail', exc))
        if isinstance(exc, asyncio.CancelledError):
            save(token, owner, state, 'running')
            raise
        save(token, owner, state, 'paused')
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)


def launch(token, user_id, resume=False):
    owner = str(uuid.uuid4())
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT status,lease FROM jobs WHERE id=? AND user_id=?', (str(token), user_id)).fetchone()
        if not row or row['status'] == 'finished' or (row['status'] == 'paused' and not resume):
            return
        if row['status'] == 'running' and row['lease'] > time.time():
            return
        db.execute("UPDATE jobs SET owner=?,lease=?,status='running' WHERE id=?", (owner, time.time()+LEASE_SECONDS, str(token)))
    task = asyncio.create_task(run_job(str(token), user_id, owner))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def public_job(job):
    if not job:
        return None
    fields = ('id', 'status', 'root_url', 'include_attachments', 'processed', 'batches',
              'files', 'warnings', 'notices', 'failures', 'error', 'started_at', 'progress',
              'skipped_files', 'replaced_documents', 'page_count')
    return {**{key: job[key] for key in fields}, 'pending_count': len(job['pending']), 'batch_size': BATCH_SIZE}
