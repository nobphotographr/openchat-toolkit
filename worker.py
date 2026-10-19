"""One bounded read-only LINE history collection per run; no AI dependency."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import uuid
import collection_fallback as fallback

from collector import Archive, ROOT, InvalidExport, configured_rooms, MAX_BYTES

from runtime_lock import WorkerLock, Refused, desktop_lock_path
from settings import load_config


def now():
    return datetime.now(timezone.utc).isoformat()


from collection_schedule import select_due


def collect(archive, room, runner=subprocess.run):
    if room not in configured_rooms():
        raise InvalidExport('room_not_registered')
    exports = ROOT/'state/exports'
    exports.mkdir(parents=True, exist_ok=True)
    path = exports/(uuid.uuid4().hex+'.txt')
    started = now()
    with archive.db:
        # A killed process leaves a durable interrupted marker, never false success.
        archive.db.execute("UPDATE attempts SET status='interrupted',finished_at=?,reason='previous_process_ended' WHERE status='running'", (started,))
        ident = archive.db.execute('INSERT INTO attempts(room,started_at,status,reason) VALUES (?,?,?,?)',
            (room, started, 'running', path.name)).lastrowid
    result = {'status': 'stopped', 'room': room, 'send_invocations': 0}
    try:
        from collection_retry import consume
        consume(archive.db,room,datetime.now(timezone.utc))
        config=json.loads((ROOT/'collector-config.json').read_text(encoding='utf-8-sig'))
        policy=next(r for r in config['rooms'] if r['name']==room)
        remembered = fallback.screen_fallback(archive.db, room)
        if policy.get('collection_method')=='screen_copy' or (remembered and policy.get('collection_method') != 'manual'):
            if remembered:
                result.update(fallback_reason='line_download_failed', reason='download_failed_using_screen')
            return fallback.read_screen(archive, room, runner, ROOT/'native/LineHistory.exe', result)
        if policy.get('pc_history_download',True) is False:
            result.update(status='awaiting_screen_reader',reason='pc_history_download_disabled')
            return result
        with archive.db:
            archive.db.execute('INSERT INTO exports(attempt_id,room,path) VALUES (?,?,?)', (ident,room,str(path)))
        native = runner([str(ROOT/'native/LineHistory.exe'), '--export', room, str(path)],
                        capture_output=True, timeout=50, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for line in native.stderr.decode('utf-8-sig', errors='replace').splitlines():
            try:
                cleanup = json.loads(line)
                if isinstance(cleanup,dict) and cleanup.get('kind') == 'window_cleanup':
                    result['window_cleanup'] = cleanup
            except json.JSONDecodeError:
                pass
        try:
            reply = json.loads(native.stdout.decode('utf-8-sig'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise InvalidExport('native_result_unreadable') from None
        if not isinstance(reply, dict):
            raise InvalidExport('native_result_unreadable')
        if fallback.confirmed_failure(native, reply, result, room):
            fallback.remember(archive.db, room)
            result.update(fallback_reason='line_download_failed', reason='download_failed_using_screen',
                          export_cleanup=result.get('window_cleanup'))
            return fallback.read_screen(archive, room, runner, ROOT/'native/LineHistory.exe', result)
        if native.returncode != 0 or reply.get('status') != 'exported':
            reason = reply.get('reason', 'native_export_failed')
            if reason == 'exact_window_not_unique':
                try: result['window_diagnostic'] = json.loads(native.stderr.decode('utf-8-sig'))
                except (UnicodeDecodeError,json.JSONDecodeError): pass
            raise InvalidExport(reason if isinstance(reason,str) and len(reason)<120 else 'native_export_failed')
        if reply.get('room') != room or Path(reply.get('path','')).resolve() != path.resolve() or reply.get('send_invocations') != 0:
            raise InvalidExport('native_result_identity_mismatch')
        with archive.db:
            archive.db.execute('UPDATE exports SET receipt=? WHERE attempt_id=?', (json.dumps(reply,ensure_ascii=False),ident))
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
            raise InvalidExport('export_file_invalid')
        result.update(archive.ingest(room, path.read_bytes(), expected_room=reply['room']))
        # The exact bytes are committed in snapshots before deleting our new
        # temporary export; repeated reads must not accumulate copies.
        with archive.db:
            archive.db.execute('UPDATE exports SET sha256=? WHERE attempt_id=?', (result['sha256'],ident))
        path.unlink()
    except subprocess.TimeoutExpired:
        result['reason'] = 'native_timeout_review_dialog'
        # The timed-out helper was killed, so its finally block could not run.
        # This close-only helper never opens a room or changes any draft/dialog.
        try:
            closed = runner([str(ROOT/'native/LineHistory.exe'), '--close', room],
                            capture_output=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            reply = json.loads(closed.stdout.decode('utf-8-sig'))
            result['window_cleanup'] = {'status': reply.get('status'), 'reason': reply.get('reason')}
        except Exception:
            result['window_cleanup'] = {'status':'unconfirmed'}
    except InvalidExport as error:
        result['reason'] = str(error)
    except Exception as error:
        result['reason'] = type(error).__name__
    finally:
        with archive.db:
            archive.db.execute('UPDATE attempts SET finished_at=?,status=?,reason=? WHERE id=?',
                (now(), result['status'], result.get('reason'), ident))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--room', help='Explicit registered room, including test room; ignores scheduler enable flag')
    args = parser.parse_args()
    config = load_config()
    result = {'status': 'disabled', 'send_invocations': 0}
    try:
        with WorkerLock(ROOT/'state/collector-worker'):
            if args.room or config['enabled']:
                # All toolkit installations in this Windows account share the desktop.
                with WorkerLock(desktop_lock_path()):
                    archive = Archive(ROOT/'state/history.sqlite3')
                    try:
                        room = args.room or select_due(archive, config, datetime.now(timezone.utc))
                        if room:
                            from session_probe import probe
                            session = probe()
                            result = collect(archive, room) if session['ready'] else dict(status='deferred', reason=session['reason'], room=room, send_invocations=0)
                        else:
                            result = {'status': 'not_due', 'send_invocations': 0}
                    finally:
                        archive.close()
    except Refused:
        result = {'status': 'busy', 'send_invocations': 0}
    except Exception as error:
        result = {'status': 'stopped', 'reason': type(error).__name__, 'send_invocations': 0}
    result['observed_at'] = now()
    if result['status'] != 'busy':
        temporary = ROOT/'state'/('collector-status-'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(ROOT/'state/collector-status.json')
    if sys.stdout:
        if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))
    return 2 if result['status'] == 'stopped' else 0


if __name__ == '__main__':
    raise SystemExit(main())
