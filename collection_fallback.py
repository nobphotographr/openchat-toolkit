"""Persist a confirmed download failure; never turn unknown UI errors into success."""
import json
import subprocess
from datetime import datetime, timezone
from collector import InvalidExport


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS collection_routes (
        room TEXT PRIMARY KEY, method TEXT NOT NULL, reason TEXT NOT NULL,
        updated_at TEXT NOT NULL)''')


def screen_fallback(db, room):
    initialize(db)
    return db.execute("SELECT 1 FROM collection_routes WHERE room=? AND method='screen_copy'", (room,)).fetchone() is not None


def remember(db, room):
    initialize(db)
    with db:
        db.execute('INSERT OR IGNORE INTO collection_routes VALUES (?,?,?,?)',
                   (room, 'screen_copy', 'line_download_failed', datetime.now(timezone.utc).isoformat()))


def cleanup(native, result):
    for line in native.stderr.decode('utf-8-sig', errors='replace').splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict) and value.get('kind') == 'window_cleanup':
                result['window_cleanup'] = value
        except json.JSONDecodeError:
            pass


def confirmed_failure(native, reply, result, room):
    return (native.returncode != 0 and isinstance(reply, dict)
            and reply.get('status') == 'stopped' and reply.get('room') == room
            and type(reply.get('send_invocations')) is int and reply['send_invocations'] == 0
            and reply.get('reason') == 'line_download_failed'
            and result.get('window_cleanup', {}).get('status') in ('closed', 'already_closed'))


def read_screen(archive, room, runner, executable, result):
    from screen_archive import initialize as initialize_screen
    import screen_resume
    initialize_screen(archive.db)
    ready = screen_resume.finish_ready(archive, room)
    if ready is not None:
        result.update(ready, collection_method='screen_copy'); return result
    step = screen_resume.request(archive.db, room)
    checkpoint = archive.db.execute('SELECT hashes FROM screen_checkpoints WHERE room=?', (room,)).fetchone()
    command = [str(executable), '--read-screen', room]
    if checkpoint:
        command.append(','.join(json.loads(checkpoint[0])[:3]))
    if step:
        if not checkpoint: raise InvalidExport('screen_resume_checkpoint_missing')
        command.append(str(step))
    result['collection_method'] = 'screen_copy'
    # The export cleanup is not evidence that the subsequent screen read closed.
    result.pop('window_cleanup', None)
    native = runner(command, capture_output=True, timeout=90,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    cleanup(native, result)
    try:
        reply = json.loads(native.stdout.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InvalidExport('screen_native_result_unreadable') from None
    if not isinstance(reply, dict):
        raise InvalidExport('screen_native_result_unreadable')
    if native.returncode != 0 or reply.get('status') != 'screen_read':
        reason = reply.get('reason', 'screen_native_failed')
        raise InvalidExport(reason if isinstance(reason, str) and len(reason) < 120 else 'screen_native_failed')
    result.update(screen_resume.ingest(archive, room, reply, step))
    return result
