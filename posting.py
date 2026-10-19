"""Local, durable one-shot posting queue. Only explicitly scheduled drafts run."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import uuid

ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))
LABELS = {'draft':'下書き','scheduled':'予約中','preparing':'送信準備中',
          'send_intent':'送信操作中','local_confirmed':'LINE画面に表示済み',
          'needs_review':'要確認（再送停止）','blocked':'準備で停止',
          'expired':'期限切れ','cancelled':'取消済み','review_closed':'確認終了（再送なし）'}


def now(): return datetime.now(JST)


def rooms():
    from settings import load_config
    config=load_config()
    return [r['name'] for r in config['rooms']]


def validate(room, body, image_id=''):
    if room not in rooms(): raise ValueError('登録済みの宛先を選んでください。')
    body=body.replace('\r\n','\n')
    if (not body.strip() and not image_id) or len(body.encode('utf-16-le'))//2>4000:
        raise ValueError('本文は空白以外の文字を含む4,000文字以内にしてください。')
    if any((ord(c)<32 and c!='\n') or ord(c)==127 for c in body):
        raise ValueError('本文に使用できない制御文字があります。')
    return body


def effective_time(value):
    if value.tzinfo is None: raise ValueError('日時にはタイムゾーンが必要です。')
    value=value.astimezone(JST)
    if value.hour<8: return value.replace(hour=8,minute=0,second=0,microsecond=0)
    if value.hour>=21: return (value+timedelta(days=1)).replace(hour=8,minute=0,second=0,microsecond=0)
    return value


def signature(row):
    content=[row['id'],row['room'],row['body'],row['due_at']]
    if 'image_id' in row.keys() and row['image_id']:content.append(row['image_id'])
    return hashlib.sha256(json.dumps(content,ensure_ascii=False).encode()).hexdigest()


class Posts:
    def __init__(self,path=None):
        self.path=Path(path or ROOT/'state/posting.sqlite3'); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,timeout=5); self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL'); self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY,room TEXT NOT NULL,body TEXT NOT NULL,
            due_at TEXT,status TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,
            approval TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,reason TEXT);
          CREATE TABLE IF NOT EXISTS post_events(id INTEGER PRIMARY KEY,post_id TEXT,at TEXT,status TEXT,reason TEXT);
          CREATE TABLE IF NOT EXISTS post_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          INSERT OR IGNORE INTO post_settings VALUES('paused','1');
          CREATE TABLE IF NOT EXISTS post_templates(name TEXT PRIMARY KEY,body TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS post_parts(post_id TEXT,part TEXT,status TEXT,updated_at TEXT,PRIMARY KEY(post_id,part));
        ''')
        if 'image_id' not in {r[1] for r in self.db.execute('PRAGMA table_info(posts)')}:
            try:self.db.execute("ALTER TABLE posts ADD COLUMN image_id TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError as error:
                if 'duplicate column' not in str(error):raise
        from recurrence import initialize
        initialize(self.db)
        from templates import initialize as initialize_templates
        initialize_templates(self.db)

    def close(self): self.db.close()
    def get(self,ident):
        row=self.db.execute('SELECT * FROM posts WHERE id=?',(ident,)).fetchone()
        if row is None: raise ValueError('投稿が見つかりません。')
        return dict(row)
    def list(self):
        result=[dict(r) for r in self.db.execute('SELECT p.*,o.rule_id AS recurrence_id FROM posts p LEFT JOIN recurring_occurrences o ON o.post_id=p.id ORDER BY p.created_at DESC')]
        for row in result:row['parts']=[dict(p) for p in self.db.execute('SELECT part,status,updated_at FROM post_parts WHERE post_id=? ORDER BY updated_at',(row['id'],))]
        return result
    def validate(self,row):
        body=validate(row['room'],row['body'],row.get('image_id',''))
        if row.get('image_id'):
            from media import read
            read(self.path.parent,row['image_id'])
        return body
    def audit(self,ident,status,reason=None):
        self.db.execute('INSERT INTO post_events(post_id,at,status,reason) VALUES(?,?,?,?)',(ident,now().isoformat(),status,reason))
    def paused(self): return self.db.execute("SELECT value FROM post_settings WHERE key='paused'").fetchone()[0]=='1'
    def pause(self,value):
        with self.db: self.db.execute("UPDATE post_settings SET value=? WHERE key='paused'",('1' if value else '0',))

    def save(self,room,body,ident=None,version=None,image_id=''):
        body=self.validate(dict(room=room,body=body,image_id=image_id)); stamp=now().isoformat()
        with self.db:
            if ident:
                changed=self.db.execute("UPDATE posts SET room=?,body=?,image_id=?,version=version+1,updated_at=? WHERE id=? AND status='draft' AND version=?",(room,body,image_id,stamp,ident,version)).rowcount
                if changed!=1: raise ValueError('別の操作で変更されています。一覧を更新してください。')
            else:
                ident=uuid.uuid4().hex
                self.db.execute("INSERT INTO posts(id,room,body,image_id,status,created_at,updated_at) VALUES(?,?,?,?,'draft',?,?)",(ident,room,body,image_id,stamp,stamp))
            self.audit(ident,'draft')
        return self.get(ident)

    def schedule(self,ident,version,requested,current=None):
        current=current or now()
        if requested.tzinfo is None or requested<current-timedelta(seconds=5): raise ValueError('未来の日時を指定してください。')
        due=effective_time(requested).isoformat()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.get(ident)
            if row['status']!='draft' or row['version']!=version: raise ValueError('下書きが変更されています。')
            self.validate(row); row['due_at']=due
            # Double clicks / a second editor must not create the same reservation.
            duplicate=self.db.execute("SELECT 1 FROM posts WHERE room=? AND body=? AND due_at=? AND image_id=? AND status NOT IN ('draft','cancelled')",(row['room'],row['body'],due,row['image_id'])).fetchone()
            if duplicate: raise ValueError('同じ宛先・本文・日時の予約が既にあります。')
            self.db.execute("UPDATE posts SET due_at=?,approval=?,status='scheduled',version=version+1,updated_at=? WHERE id=?",(due,signature(row),now().isoformat(),ident))
            self.audit(ident,'scheduled')
        return self.get(ident)

    def change(self,ident,version,target):
        allowed={'draft':('scheduled','blocked','expired'), 'cancelled':('draft','scheduled','blocked','expired'), 'review_closed':('needs_review',)}
        if target not in allowed: raise ValueError('許可されていない操作です。')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE'); row=self.get(ident)
            if row['version']!=version or row['status'] not in allowed[target]: raise ValueError('現在の状態では操作できません。一覧を更新してください。')
            if target=='draft' and self.db.execute('SELECT 1 FROM recurring_occurrences WHERE post_id=?',(ident,)).fetchone():
                raise ValueError('繰り返し予約の1回分は取消できます。内容を変える場合は新しい下書きを作成してください。')
            self.db.execute('UPDATE posts SET status=?,approval=NULL,version=version+1,updated_at=? WHERE id=?',(target,now().isoformat(),ident))
            self.audit(ident,target,'user_action')

    def finish(self,ident,status,reason=None):
        with self.db:
            self.db.execute('UPDATE posts SET status=?,reason=?,version=version+1,updated_at=? WHERE id=?',(status,reason,now().isoformat(),ident))
            self.audit(ident,status,reason)

    def recover(self):
        # Called only by the worker while holding its process lock, never by GUI.
        with self.db:
            for r in self.db.execute("SELECT id FROM posts WHERE status IN ('preparing','send_intent')").fetchall():
                self.db.execute("UPDATE posts SET status='needs_review',reason='process_interrupted',version=version+1 WHERE id=?",(r['id'],))
                self.audit(r['id'],'needs_review','process_interrupted')

    def claim(self,current=None):
        current=current or now()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for row in self.db.execute("SELECT id,due_at FROM posts WHERE status='scheduled'").fetchall():
                if current-datetime.fromisoformat(row['due_at'])>timedelta(hours=24):
                    self.db.execute("UPDATE posts SET status='expired',version=version+1,reason='over_24_hours' WHERE id=?",(row['id'],))
                    self.audit(row['id'],'expired','over_24_hours')
            if self.paused() or not 8<=current.astimezone(JST).hour<21: return None
            from recurrence import permits
            row=next((r for r in self.db.execute("SELECT * FROM posts WHERE status='scheduled' AND due_at<=? ORDER BY due_at,created_at",(current.astimezone(JST).isoformat(),)).fetchall() if permits(self,r)),None)
            if row is None: return None
            row=dict(row)
            try:self.validate(row)
            except ValueError:
                self.db.execute("UPDATE posts SET status='blocked',reason='content_validation_failed',version=version+1 WHERE id=?",(row['id'],));self.audit(row['id'],'blocked','content_validation_failed');return None
            if row['approval']!=signature(row):
                self.db.execute("UPDATE posts SET status='blocked',reason='approval_mismatch',version=version+1 WHERE id=?",(row['id'],)); self.audit(row['id'],'blocked','approval_mismatch'); return None
            self.db.execute("UPDATE posts SET status='preparing',version=version+1 WHERE id=?",(row['id'],)); self.audit(row['id'],'preparing')
            return row

    def template(self,name,body):
        from templates import save
        old=self.db.execute('SELECT version FROM post_templates WHERE name=?',(name.strip(),)).fetchone()
        return save(self,name,body,original=name.strip() if old else None,version=old[0] if old else None)


def dispatch(store,adapter,current=None):
    row=store.claim(current)
    if row is None: return {'status':'idle','send_invocations':0}
    if row.get('image_id'):
        from media_delivery import dispatch_image
        return dispatch_image(store,row,adapter)
    ident=row['id']; phase='preflight'; sends=0
    try:
        adapter.preflight(row['room'],row['body'])
        phase='prepare'; adapter.prepare(row['room'],row['body']); adapter.verify_prepared(row['room'],row['body'])
        # Commit before the operation, including the case of a lost native reply.
        store.finish(ident,'send_intent'); phase='invoke'; sends=1
        adapter.invoke(row['room'],row['body'])
        status='local_confirmed' if adapter.confirm(row['room'],row['body']) else 'needs_review'
        store.finish(ident,status,None if status=='local_confirmed' else 'local_confirmation_missing')
    except Exception as exc:
        status='blocked' if phase=='preflight' else 'needs_review'
        code=str(exc) if type(exc).__name__=='Refused' and re.fullmatch('[a-z_0-9]{1,100}',str(exc)) else type(exc).__name__
        store.finish(ident,status,phase+':'+code)
    finally:
        try: adapter.close()
        except Exception: pass
    return {'status':status,'id':ident,'send_invocations':sends}
