"""Explicit, durable one-shot screen retries, consumed only by the locked worker."""
from datetime import datetime, timezone, timedelta
import json
import screen_resume as resume


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS collection_retry_requests (
      room TEXT PRIMARY KEY, job_id TEXT NOT NULL, requested_at TEXT NOT NULL,
      state TEXT NOT NULL, consumed_at TEXT)''')


def eligible(db, room):
    state=resume.job(db,room)
    return bool(state and state['phase']=='review' and state['stalls']>=3
                and not state['seeking'] and state['next_step']<resume.MAX_SCROLL_STEPS
                and len(json.loads(state['messages']))<=resume.MAX_MESSAGES
                and len(state['messages'].encode('utf8'))<=resume.MAX_BYTES
                and json.loads(state['checkpoint'])==resume.checkpoint(db,room))


def request(db, room, config, current=None):
    current=current or datetime.now(timezone.utc)
    policy=next((r for r in config['rooms'] if r['name']==room),None)
    if not config.get('enabled') or not policy or not policy['enabled'] or policy.get('test') or policy.get('collection_method')=='manual':
        raise ValueError('この部屋の自動取得は有効になっていません。')
    if not eligible(db,room):raise ValueError('自動で再試行できない状態です。詳細を確認してください。')
    initialize(db)
    with db:
        db.execute('BEGIN IMMEDIATE')
        # job() initializes tables, so inspect it before beginning the transaction.
        state=db.execute('SELECT job_id,phase FROM screen_resume_jobs WHERE room=?',(room,)).fetchone()
        if not state or state[1]!='review':raise ValueError('状態が変わりました。取得状況を更新してください。')
        previous=db.execute('SELECT requested_at,state,job_id FROM collection_retry_requests WHERE room=?',(room,)).fetchone()
        if (previous and previous[1]=='requested' and previous[2]==state[0]
                and timedelta(0)<=current-datetime.fromisoformat(previous[0])<=timedelta(hours=24)):
            return {'state':'requested'}
        if previous and current-datetime.fromisoformat(previous[0])<timedelta(minutes=5):
            raise ValueError('前の再試行依頼から5分ほど待ってください。')
        db.execute('INSERT OR REPLACE INTO collection_retry_requests VALUES (?,?,?,?,NULL)',(room,state[0],current.isoformat(),'requested'))
    return {'state':'requested'}


def due(db, config, current):
    initialize(db)
    enabled={r['name'] for r in config['rooms'] if r['enabled'] and not r.get('test') and r.get('collection_method')!='manual'}
    for room,job_id,stamp in db.execute("SELECT room,job_id,requested_at FROM collection_retry_requests WHERE state='requested' ORDER BY requested_at").fetchall():
        state=resume.job(db,room)
        if room in enabled and timedelta(0)<=current-datetime.fromisoformat(stamp)<=timedelta(hours=24) and state and state['job_id']==job_id and eligible(db,room):
            return room
    return None


def consume(db, room, current):
    initialize(db)
    valid=eligible(db,room)
    state=resume.job(db,room)
    with db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT job_id,requested_at FROM collection_retry_requests WHERE room=? AND state='requested'",(room,)).fetchone()
        if not row:return False
        fresh=timedelta(0)<=current-datetime.fromisoformat(row[1])<=timedelta(hours=24)
        if not (valid and state and row[0]==state['job_id'] and fresh):
            db.execute("UPDATE collection_retry_requests SET state='expired' WHERE room=?",(room,));return False
        # Preserve the complete buffer, original checkpoint and resume cursor.
        db.execute("UPDATE screen_resume_jobs SET phase='pending',stalls=0 WHERE room=? AND job_id=?",(room,row[0]))
        db.execute("UPDATE collection_retry_requests SET state='consumed',consumed_at=? WHERE room=?",(current.isoformat(),room))
    return True
