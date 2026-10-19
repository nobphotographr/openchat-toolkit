"""Approved recurring rules expanded into ordinary durable posting jobs."""
from datetime import date,datetime,time,timedelta
import hashlib
import json
import uuid
from posting import JST, now, validate, signature


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS recurring_rules(
        id TEXT PRIMARY KEY, room TEXT NOT NULL, body TEXT NOT NULL, spec TEXT NOT NULL,
        status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, approval TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS recurring_occurrences(
        rule_id TEXT NOT NULL, business_date TEXT NOT NULL, post_id TEXT NOT NULL UNIQUE,
        PRIMARY KEY(rule_id,business_date));
    ''')
    if 'image_id' not in {r[1] for r in db.execute('PRAGMA table_info(recurring_rules)')}:
        try:db.execute("ALTER TABLE recurring_rules ADD COLUMN image_id TEXT NOT NULL DEFAULT ''")
        except Exception as error:
            if 'duplicate column' not in str(error):raise


def normalize(spec):
    if not isinstance(spec,dict) or set(spec)!={'weekdays','time','start','end'}: raise ValueError('繰り返し条件を確認してください。')
    days=spec['weekdays']
    if not isinstance(days,list) or not days or any(type(d) is not int or d not in range(7) for d in days):
        raise ValueError('曜日を1つ以上選んでください。')
    if len(days)!=len(set(days)): raise ValueError('曜日が重複しています。')
    try:
        at=time.fromisoformat(spec['time'])
        start=date.fromisoformat(spec['start']);end=date.fromisoformat(spec['end']) if spec['end'] else None
    except (ValueError,TypeError): raise ValueError('繰り返しの日時を確認してください。') from None
    if at.tzinfo or at.second or at.microsecond or not 8<=at.hour<21 or len(spec['time'])!=5:
        raise ValueError('繰り返し時刻は8:00〜20:59の間で指定してください。')
    if end and end<start: raise ValueError('終了日は開始日以降にしてください。')
    return dict(weekdays=sorted(days),time=at.strftime('%H:%M'),start=start.isoformat(),end=end.isoformat() if end else '')


def canonical(spec):return json.dumps(normalize(spec),ensure_ascii=False,sort_keys=True,separators=(',',':'))


def digest(rule):
    content=[rule['id'],rule['room'],rule['body'],rule['spec']]
    if rule.get('image_id'):content.append(rule['image_id'])
    return hashlib.sha256(json.dumps(content,ensure_ascii=False).encode()).hexdigest()


def dates(spec,current,days=14):
    spec=normalize(spec);current=current.astimezone(JST)
    first=max(date.fromisoformat(spec['start']),current.date())
    end=date.fromisoformat(spec['end']) if spec['end'] else None
    # End bound is relative to the candidate start, also supporting a future start.
    for offset in range(days):
        day=first+timedelta(days=offset)
        if end and day>end: break
        candidate=datetime.combine(day,time.fromisoformat(spec['time']),JST)
        if day.weekday() in spec['weekdays'] and candidate>=current: yield candidate


def get(store,ident):
    row=store.db.execute('SELECT * FROM recurring_rules WHERE id=?',(ident,)).fetchone()
    if row is None: raise ValueError('繰り返し予約が見つかりません。')
    return dict(row)


def listing(store):
    result=[]
    for row in store.db.execute('SELECT * FROM recurring_rules ORDER BY created_at DESC'):
        row=dict(row);row['spec']=json.loads(row['spec']);row.pop('approval',None)
        row['next_due']=store.db.execute("SELECT MIN(p.due_at) FROM posts p JOIN recurring_occurrences o ON o.post_id=p.id WHERE o.rule_id=? AND p.status='scheduled'",(row['id'],)).fetchone()[0]
        result.append(row)
    return result


def create(store,ident,version,spec,current=None):
    current=current or now();encoded=canonical(spec)
    if not list(dates(spec,current)): raise ValueError('指定した期間に、これから実行できる日がありません。')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE');row=store.get(ident)
        if row['status']!='draft' or row['version']!=version:raise ValueError('下書きが変更されています。')
        store.validate(row)
        duplicate=store.db.execute("SELECT 1 FROM recurring_rules WHERE room=? AND body=? AND image_id=? AND spec=? AND status!='cancelled'",(row['room'],row['body'],row['image_id'],encoded)).fetchone()
        if duplicate:raise ValueError('同じ繰り返し予約が既にあります。')
        rule=dict(id=uuid.uuid4().hex,room=row['room'],body=row['body'],spec=encoded,image_id=row['image_id'])
        stamp=current.isoformat()
        store.db.execute('INSERT INTO recurring_rules(id,room,body,image_id,spec,status,approval,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (rule['id'],rule['room'],rule['body'],rule['image_id'],encoded,'active',digest(rule),stamp,stamp))
        store.db.execute("UPDATE posts SET status='cancelled',version=version+1,approval=NULL,updated_at=?,reason='converted_to_recurring_rule' WHERE id=?",(stamp,ident))
        store.audit(ident,'cancelled','converted_to_recurring_rule')
        _materialize(store,rule['id'],current)
    return get(store,rule['id'])


def _materialize(store,ident,current):
    rule=get(store,ident)
    if rule['status']!='active':return
    if rule['approval']!=digest(rule):
        store.db.execute("UPDATE recurring_rules SET status='needs_review',version=version+1,updated_at=? WHERE id=?",(current.isoformat(),ident));return
    if store.db.execute("SELECT 1 FROM posts p JOIN recurring_occurrences o ON o.post_id=p.id WHERE o.rule_id=? AND p.status IN ('needs_review','blocked')",(ident,)).fetchone():
        store.db.execute("UPDATE recurring_rules SET status='needs_review',version=version+1,updated_at=? WHERE id=?",(current.isoformat(),ident));return
    spec=json.loads(rule['spec'])
    try:store.validate(rule)
    except ValueError:
        store.db.execute("UPDATE recurring_rules SET status='needs_review',version=version+1,updated_at=? WHERE id=?",(current.isoformat(),ident));return
    for due in dates(spec,current):
        business_date=due.date().isoformat()
        if store.db.execute('SELECT 1 FROM recurring_occurrences WHERE rule_id=? AND business_date=?',(ident,business_date)).fetchone():continue
        # Same body/room/time shared with another rule or a manual job: hold the
        # rule for review, rather than silently deliver twice or skip ownership.
        if store.db.execute("SELECT 1 FROM posts WHERE room=? AND body=? AND image_id=? AND due_at=? AND status NOT IN ('draft','cancelled')",(rule['room'],rule['body'],rule['image_id'],due.isoformat())).fetchone():
            store.db.execute("UPDATE recurring_rules SET status='needs_review',version=version+1,updated_at=? WHERE id=?",(current.isoformat(),ident));return
        post=dict(id=uuid.uuid4().hex,room=rule['room'],body=rule['body'],due_at=due.isoformat(),image_id=rule['image_id'])
        stamp=current.isoformat()
        store.db.execute("INSERT INTO posts(id,room,body,image_id,due_at,status,approval,created_at,updated_at) VALUES(?,?,?,?,?,'scheduled',?,?,?)",(post['id'],post['room'],post['body'],post['image_id'],post['due_at'],signature(post),stamp,stamp))
        store.db.execute('INSERT INTO recurring_occurrences VALUES(?,?,?)',(ident,business_date,post['id']))
        store.audit(post['id'],'scheduled','recurring_rule')
    if spec['end'] and date.fromisoformat(spec['end'])<current.astimezone(JST).date() and not store.db.execute("SELECT 1 FROM posts p JOIN recurring_occurrences o ON o.post_id=p.id WHERE o.rule_id=? AND p.status IN ('scheduled','preparing','send_intent')",(ident,)).fetchone():
        store.db.execute("UPDATE recurring_rules SET status='completed',version=version+1,updated_at=? WHERE id=?",(current.isoformat(),ident))


def materialize(store,current=None):
    current=current or now()
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        # Recurring posts missed on earlier dates are never replayed in a burst.
        cutoff=current.astimezone(JST).date().isoformat()
        for p in store.db.execute("SELECT p.id FROM posts p JOIN recurring_occurrences o ON o.post_id=p.id WHERE p.status='scheduled' AND substr(p.due_at,1,10)<?",(cutoff,)).fetchall():
            store.db.execute("UPDATE posts SET status='expired',approval=NULL,version=version+1,reason='recurring_date_missed' WHERE id=?",(p[0],));store.audit(p[0],'expired','recurring_date_missed')
        for row in store.db.execute("SELECT id FROM recurring_rules WHERE status='active'").fetchall():_materialize(store,row[0],current)


def permits(store,post):
    row=store.db.execute('SELECT r.* FROM recurring_rules r JOIN recurring_occurrences o ON o.rule_id=r.id WHERE o.post_id=?',(post['id'],)).fetchone()
    if row is None:return True
    rule=dict(row)
    if rule['status']!='active' or rule['approval']!=digest(rule):return False
    return not store.db.execute("SELECT 1 FROM posts p JOIN recurring_occurrences o ON o.post_id=p.id WHERE o.rule_id=? AND p.status IN ('needs_review','blocked')",(rule['id'],)).fetchone()


def change(store,ident,version,target,current=None):
    current=current or now()
    allowed={'paused':('active',),'active':('paused','needs_review'),'cancelled':('active','paused','needs_review')}
    with store.db:
        store.db.execute('BEGIN IMMEDIATE');rule=get(store,ident)
        if target not in allowed or rule['status'] not in allowed[target] or rule['version']!=version:raise ValueError('状態が変更されています。更新してください。')
        if store.db.execute("SELECT 1 FROM posts p JOIN recurring_occurrences o ON o.post_id=p.id WHERE o.rule_id=? AND p.status IN ('preparing','send_intent')",(ident,)).fetchone():raise ValueError('送信処理中です。完了後に操作してください。')
        if target=='active':
            if rule['approval']!=digest(rule):raise ValueError('承認した内容が変わっています。再開できません。')
            if store.db.execute("SELECT 1 FROM posts p JOIN recurring_occurrences o ON o.post_id=p.id WHERE o.rule_id=? AND p.status IN ('needs_review','blocked')",(ident,)).fetchone():raise ValueError('停止原因の投稿を確認・取消してから再開してください。')
            # Resuming must never flush posts missed while the rule was paused.
            for p in store.db.execute("SELECT p.id FROM posts p JOIN recurring_occurrences o ON o.post_id=p.id WHERE o.rule_id=? AND p.status='scheduled' AND p.due_at<?",(ident,current.isoformat())).fetchall():
                store.db.execute("UPDATE posts SET status='expired',approval=NULL,version=version+1,reason='recurring_pause_skipped' WHERE id=?",(p[0],));store.audit(p[0],'expired','recurring_pause_skipped')
        if target=='cancelled':
            for p in store.db.execute("SELECT p.id FROM posts p JOIN recurring_occurrences o ON o.post_id=p.id WHERE o.rule_id=? AND p.status='scheduled'",(ident,)).fetchall():
                store.db.execute("UPDATE posts SET status='cancelled',approval=NULL,version=version+1 WHERE id=?",(p[0],));store.audit(p[0],'cancelled','recurring_rule_cancelled')
        store.db.execute('UPDATE recurring_rules SET status=?,version=version+1,updated_at=? WHERE id=?',(target,current.isoformat(),ident))
        if target=='active':_materialize(store,ident,current)
    return get(store,ident)
