"""Local search and durable keyword inbox. Does not access LINE or the network."""
from datetime import date
import re
from urllib.parse import urlsplit


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS record_sources (
      room TEXT NOT NULL, fingerprint TEXT NOT NULL, occurrence INTEGER NOT NULL,
      source TEXT NOT NULL, time_kind TEXT NOT NULL,
      PRIMARY KEY(room,fingerprint,occurrence),
      FOREIGN KEY(room,fingerprint,occurrence) REFERENCES records(room,fingerprint,occurrence));
    CREATE TABLE IF NOT EXISTS keyword_rules (
      id INTEGER PRIMARY KEY, term TEXT NOT NULL COLLATE NOCASE,
      room TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
      cursor INTEGER NOT NULL, UNIQUE(term,room));
    CREATE TABLE IF NOT EXISTS keyword_hits (
      id INTEGER PRIMARY KEY, rule_id INTEGER NOT NULL,
      room TEXT NOT NULL, fingerprint TEXT NOT NULL, occurrence INTEGER NOT NULL,
      is_read INTEGER NOT NULL DEFAULT 0,
      UNIQUE(rule_id,room,fingerprint,occurrence),
      FOREIGN KEY(rule_id) REFERENCES keyword_rules(id),
      FOREIGN KEY(room,fingerprint,occurrence) REFERENCES records(room,fingerprint,occurrence));
    CREATE TABLE IF NOT EXISTS bookmarks (
      room TEXT NOT NULL, fingerprint TEXT NOT NULL, occurrence INTEGER NOT NULL,
      PRIMARY KEY(room,fingerprint,occurrence),
      FOREIGN KEY(room,fingerprint,occurrence) REFERENCES records(room,fingerprint,occurrence));
    CREATE INDEX IF NOT EXISTS records_date_room ON records(timestamp,room);
    ''')


def add_rule(db, term, room='', include_history=False):
    term = term.strip()
    if not term or len(term) > 100:
        raise ValueError('キーワードは1〜100文字で入力してください。')
    # Immediate transaction fixes the boundary against a simultaneous import.
    db.execute('BEGIN IMMEDIATE')
    try:
        cursor = 0 if include_history else db.execute('SELECT COALESCE(MAX(rowid),0) FROM records').fetchone()[0]
        db.execute('INSERT INTO keyword_rules(term,room,cursor) VALUES (?,?,?)', (term,room,cursor))
        db.commit()
    except Exception:
        db.rollback()
        raise
    refresh(db)


def refresh(db):
    """Hits and cursor advance atomically; reruns cannot lose or duplicate hits."""
    with db:
        db.execute('BEGIN IMMEDIATE')
        end = db.execute('SELECT COALESCE(MAX(rowid),0) FROM records').fetchone()[0]
        for ident,term,room,cursor in db.execute('SELECT id,term,room,cursor FROM keyword_rules WHERE enabled=1').fetchall():
            db.execute('''INSERT OR IGNORE INTO keyword_hits(rule_id,room,fingerprint,occurrence)
                SELECT ?,room,fingerprint,occurrence FROM records
                WHERE rowid>? AND rowid<=? AND (?='' OR room=?)
                  AND instr(lower(raw_text),lower(?))>0''', (ident,cursor,end,room,room,term))
            db.execute('UPDATE keyword_rules SET cursor=? WHERE id=?', (end,ident))


def links(text):
    # Only explicit HTTP(S) links. Never resolve URLs or download their contents.
    found = []
    for value in re.findall(r'https?://[^\s<>"\x00-\x1f]+', text, re.I):
        value = value.rstrip('。、，．！？!?,;:）】」』〉》')
        while value.endswith(')') and value.count(')') > value.count('('):
            value = value[:-1]
        try:
            parsed = urlsplit(value)
            if parsed.scheme.lower() not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
                continue
        except ValueError:
            continue
        if value not in found:
            found.append(value)
    return found


def search(db, *, query='', room='', start='', end='', mode='history', limit=300, offset=0):
    if mode not in ('history','links','inbox','unread','bookmarks'):
        raise ValueError('invalid_mode')
    for value in (start,end):
        if value and date.fromisoformat(value).isoformat() != value:
            raise ValueError('日付はYYYY-MM-DDで入力してください。')
    if start and end and start>end:
        raise ValueError('開始日は終了日以前にしてください。')
    limit = min(max(int(limit),1),500)
    offset = max(int(offset),0)
    conditions, args = ['1=1'], []
    for clause,value in [('instr(lower(r.raw_text),lower(?))>0',query),('r.room=?',room),('r.timestamp>=?',start),('substr(r.timestamp,1,10)<=?',end)]:
        if value:
            conditions.append(clause); args.append(value)
    if mode in ('inbox','unread'):
        read = ' AND h.is_read=0' if mode=='unread' else ''
        conditions.append('EXISTS (SELECT 1 FROM keyword_hits h WHERE h.room=r.room AND h.fingerprint=r.fingerprint AND h.occurrence=r.occurrence'+read+')')
    if mode=='bookmarks':
        conditions.append('EXISTS (SELECT 1 FROM bookmarks b WHERE b.room=r.room AND b.fingerprint=r.fingerprint AND b.occurrence=r.occurrence)')
    if mode=='links':
        conditions.append("(instr(lower(r.raw_text),'https://')>0 OR instr(lower(r.raw_text),'http://')>0)")
    where = ' AND '.join(conditions)
    count = db.execute('SELECT COUNT(*) FROM records r WHERE '+where,args).fetchone()[0]
    rows = db.execute("SELECT r.room,r.fingerprint,r.occurrence,r.timestamp,r.raw_text,COALESCE(s.time_kind,'message'),EXISTS(SELECT 1 FROM bookmarks b WHERE b.room=r.room AND b.fingerprint=r.fingerprint AND b.occurrence=r.occurrence) FROM records r LEFT JOIN record_sources s ON s.room=r.room AND s.fingerprint=r.fingerprint AND s.occurrence=r.occurrence WHERE "+where+' ORDER BY r.timestamp DESC,r.rowid DESC LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
    return count,[dict(zip(('room','fingerprint','occurrence','timestamp','text','timestamp_kind','saved'),r)) for r in rows]


def mark_read(db, record):
    with db:
        db.execute('UPDATE keyword_hits SET is_read=1 WHERE room=? AND fingerprint=? AND occurrence=?', identity(record))


def identity(record):
    return record['room'],record['fingerprint'],record['occurrence']


def toggle_bookmark(db, record):
    with db:
        key = identity(record)
        exists = db.execute('SELECT 1 FROM bookmarks WHERE room=? AND fingerprint=? AND occurrence=?',key).fetchone()
        if exists:
            db.execute('DELETE FROM bookmarks WHERE room=? AND fingerprint=? AND occurrence=?',key)
        else:
            db.execute('INSERT INTO bookmarks VALUES (?,?,?)',key)
    return not bool(exists)
