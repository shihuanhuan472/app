"""Small atomic progress snapshots shared by API workers; no credentials stored."""
import json
import logging
import os
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def progress_path(user_id, token):
    token = str(uuid.UUID(str(token)))
    return Path('.runtime/confluence-progress') / f'{int(user_id)}-{token}.json'


def write_progress(user_id, token, data):
    path = progress_path(user_id, token)
    temporary = path.with_name(f'{path.stem}-{uuid.uuid4().hex}.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps({**data, 'updated_at': time.time()}, ensure_ascii=False), encoding='utf-8')
        for attempt in range(4):
            try:
                os.replace(temporary, path)
                return True
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.025 * (attempt + 1))
    except OSError:
        logger.warning('Progress snapshot unavailable; import will continue', exc_info=True)
        return False
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def read_progress(user_id, token):
    path = progress_path(user_id, token)
    try:
        if time.time() - path.stat().st_mtime > 86400:
            return {}
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
