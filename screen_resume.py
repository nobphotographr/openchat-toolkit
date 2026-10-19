"""Durable screen chunks; only commit after a unique connection to saved history."""
import json
import uuid
from collector import InvalidExport
import screen_archive as screen

MAX_SCROLL_STEPS = 120
MAX_MESSAGES = 4000
MAX_BYTES = 12 * 1024 * 1024


def initialize(db):
    screen.initialize(db)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS screen_resume_jobs (
      room TEXT PRIMARY KEY, job_id TEXT NOT NULL, checkpoint TEXT NOT NULL,
      messages TEXT NOT NULL, observed_at TEXT NOT NULL, next_step INTEGER NOT NULL,
      phase TEXT NOT NULL, updated_at TEXT NOT NULL, stalls INTEGER NOT NULL DEFAULT 0,
      seeking INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS screen_resume_chunks (
      id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, room TEXT NOT NULL,
      observed_at TEXT NOT NULL, payload TEXT NOT NULL, decision TEXT NOT NULL);
    ''')
    if 'seeking' not in {row[1] for row in db.execute('PRAGMA table_info(screen_resume_jobs)')}:
        db.execute('ALTER TABLE screen_resume_jobs ADD COLUMN seeking INTEGER NOT NULL DEFAULT 0')


def checkpoint(db, room):
    row = db.execute('SELECT hashes,observed_at,sequence FROM screen_checkpoints WHERE room=?', (room,)).fetchone()
    return list(row) if row else None


def job(db, room):
    initialize(db)
    row = db.execute('SELECT job_id,checkpoint,messages,observed_at,next_step,phase,updated_at,stalls,seeking FROM screen_resume_jobs WHERE room=?', (room,)).fetchone()
    return dict(zip(('job_id','checkpoint','messages','observed_at','next_step','phase','updated_at','stalls','seeking'), row)) if row else None


def request(db, room):
    state = job(db, room)
    if state:
        if state['phase'] != 'pending':
            raise InvalidExport('screen_resume_needs_review')
        if json.loads(state['checkpoint']) != checkpoint(db, room):
            raise InvalidExport('screen_resume_checkpoint_changed')
        return state['next_step']
    return 0


def finish_ready(archive, room):
    state=job(archive.db,room)
    if not state or state['phase']!='pending': return None
    current=checkpoint(archive.db,room)
    if json.loads(state['checkpoint'])!=current:
        raise InvalidExport('screen_resume_checkpoint_changed')
    messages=json.loads(state['messages'])
    if current and screen.locate_anchor(json.loads(current[0]),[m['sha256'] for m in messages]) is not None:
        return screen.ingest_resumed(archive,room,messages,state['updated_at'],state['job_id'],current)
    return None


def anchor_positions(needle, haystack):
    return [i for i in range(len(haystack)-len(needle)+1) if haystack[i:i+len(needle)] == needle]


def _receipt(status, messages, step, **extra):
    return dict(status=status, new_records=0, observed_texts=len(messages),
                buffered_texts=len(messages), next_scroll_steps=step,
                continuity='not_confirmed', timestamp_kind='observed', **extra)


def ingest(archive, room, payload, requested_step=0):
    observed = screen.validate(payload, room)
    db = archive.db; initialize(db); state = job(db, room)
    if 'start_scroll_steps' not in payload:
        if state: raise InvalidExport('screen_resume_protocol_missing')
        return screen.ingest(archive, room, payload)
    start, step, more = payload.get('start_scroll_steps'), payload.get('next_scroll_steps'), payload.get('has_more')
    if (type(start) is not int or start != requested_step or not 0 <= start <= MAX_SCROLL_STEPS
            or type(step) is not int or not start <= step <= min(start+5, MAX_SCROLL_STEPS)
            or type(more) is not bool):
        raise InvalidExport('screen_resume_cursor_invalid')
    current = checkpoint(db, room)
    if state and json.loads(state['checkpoint']) != current:
        raise InvalidExport('screen_resume_checkpoint_changed')
    if state and (state['phase']!='pending' or start!=state['next_step']):
        raise InvalidExport('screen_resume_state_changed')
    messages, gaps = screen.combine(payload['pages'])
    # UI runtime IDs are valid only inside one process. Remove them before
    # joining chunks from different processes; use ordered text anchors instead.
    incoming = [dict(body=m['body'],sha256=m['sha256'],observed_at=observed) for m in messages]
    hashes = [m['sha256'] for m in incoming]
    known_skips = {'no_text_bubble','row_not_visible','nontext_clipboard','image_attachment','video_attachment'}
    failures = any(v for k,v in payload.get('skip_reasons',{}).items() if k not in known_skips)
    # Normal complete observations retain the original ingestion behaviour.
    if not state and (current is None or failures or gaps or
                      screen.locate_anchor(json.loads(current[0]), hashes) is not None or not more):
        return screen.ingest(archive, room, payload)
    if state and observed <= state['updated_at']:
        raise InvalidExport('screen_resume_old_observation')
    stored = json.loads(state['messages']) if state else []
    combined = stored or incoming
    phase, decision = 'pending', 'buffered'
    stalls = state['stalls'] if state else 0
    seeking = state['seeking'] if state else 0
    if failures or gaps or not incoming:
        step=start; stalls+=1; decision='unreadable_chunk'
        if stalls>=3: phase='review'
    elif state:
        tail = [m['sha256'] for m in stored[-3:]]
        matches = anchor_positions(tail, hashes) if len(tail)==3 else []
        overlap=3
        # Repeated short posts need more context, never an arbitrary match.
        for size in range(min(12,len(stored),len(hashes)),3,-1):
            longer=anchor_positions([m['sha256'] for m in stored[-size:]],hashes)
            if len(longer)==1:
                matches=longer;overlap=size;break
        if len(matches) == 1:
            seeking=0
            extra = incoming[matches[0]+overlap:]
            combined = stored + extra
            if not extra: stalls += 1
            else: stalls = 0
        else:
            # New arrivals can move the saved scroll position. Rewind and seek
            # the exact saved text boundary; never append an unconnected chunk.
            decision='seeking_saved_boundary'
            if len(matches)>1:
                phase='review';step=start
            elif not seeking:
                step=0; seeking=1; stalls=0
            elif step>start and more:
                stalls=0
            else:
                stalls+=1
                if not more or stalls>=3: phase='review'
    combined_hashes = [m['sha256'] for m in combined]
    linked = current is not None and screen.locate_anchor(json.loads(current[0]), combined_hashes) is not None
    if len(combined)>MAX_MESSAGES or len(json.dumps(combined,ensure_ascii=False).encode('utf8'))>MAX_BYTES:
        phase, decision='review','buffer_limit'
    if not linked and ((not more and not failures and not gaps) or step>=MAX_SCROLL_STEPS or stalls>=3 or len(combined)<3):
        phase, decision='review','boundary_not_reached'
    identifier = state['job_id'] if state else uuid.uuid4().hex
    initial_time = state['observed_at'] if state else observed
    with db:
        db.execute('INSERT INTO screen_resume_chunks(job_id,room,observed_at,payload,decision) VALUES (?,?,?,?,?)',
                   (identifier,room,observed,json.dumps(payload,ensure_ascii=False),decision))
        db.execute('INSERT OR REPLACE INTO screen_resume_jobs VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (room,identifier,json.dumps(current),json.dumps(combined,ensure_ascii=False),initial_time,step,phase,observed,stalls,seeking))
    if linked and phase=='pending' and not failures and not gaps:
        return screen.ingest_resumed(archive,room,combined,observed,identifier,current)
    return _receipt('screen_pending' if phase=='pending' else 'screen_resume_review', combined, step,
                    resume_job_id=identifier, resume_reason=decision)
