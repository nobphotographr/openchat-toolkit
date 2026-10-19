"""Versioned template library. Editing never changes existing posts or rules."""
import sqlite3
from posting import now,rooms


def initialize(db):
    present={r[1] for r in db.execute('PRAGMA table_info(post_templates)')}
    for name,kind in [('version','INTEGER NOT NULL DEFAULT 1'),('group_name',"TEXT NOT NULL DEFAULT ''"),('image_id',"TEXT NOT NULL DEFAULT ''"),('archived','INTEGER NOT NULL DEFAULT 0'),('updated_at',"TEXT NOT NULL DEFAULT ''")]:
        if name not in present:
            try:db.execute(f'ALTER TABLE post_templates ADD COLUMN {name} {kind}')
            except sqlite3.OperationalError as error:
                if 'duplicate column' not in str(error):raise


def listing(store,archived=None):
    return [dict(r) for r in store.db.execute('SELECT * FROM post_templates WHERE (? IS NULL OR archived=?) ORDER BY group_name,name',(archived,archived))]


def get(store,name):
    row=store.db.execute('SELECT * FROM post_templates WHERE name=?',(name,)).fetchone()
    if row is None:raise ValueError('テンプレートが見つかりません。')
    return dict(row)


def label(value,limit,empty=False):
    if not isinstance(value,str):raise ValueError('名前とグループ名を確認してください。')
    value=value.strip()
    if (not value and not empty) or len(value)>limit or any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError('テンプレート名は1〜80文字、グループ名は40文字以内で入力してください。')
    return value


def save(store,name,body,group_name='',image_id='',original=None,version=None):
    name=label(name,80);group_name=label(group_name,40,True)
    body=store.validate(dict(room=rooms()[0],body=body,image_id=image_id))
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        if original is not None:
            row=get(store,original)
            if row['version']!=version:raise ValueError('別の画面で変更されました。テンプレートを開き直してください。')
            if name!=original and store.db.execute('SELECT 1 FROM post_templates WHERE name=?',(name,)).fetchone():raise ValueError('同じ名前のテンプレートがあります。別の名前を指定してください。')
            store.db.execute('UPDATE post_templates SET name=?,body=?,group_name=?,image_id=?,version=version+1,updated_at=? WHERE name=?',(name,body,group_name,image_id,now().isoformat(),original))
        else:
            if store.db.execute('SELECT 1 FROM post_templates WHERE name=?',(name,)).fetchone():raise ValueError('同じ名前のテンプレートがあります。一覧から編集するか、別名で保存してください。')
            store.db.execute('INSERT INTO post_templates(name,body,group_name,image_id,updated_at) VALUES(?,?,?,?,?)',(name,body,group_name,image_id,now().isoformat()))
    return get(store,name)


def archive(store,name,version,value):
    if type(value) is not bool:raise ValueError('操作が不正です。')
    with store.db:
        count=store.db.execute('UPDATE post_templates SET archived=?,version=version+1,updated_at=? WHERE name=? AND version=?',(int(value),now().isoformat(),name,version)).rowcount
        if count!=1:raise ValueError('別の画面で変更されました。一覧を更新してください。')
    return get(store,name)


def duplicate(store,name,version):
    with store.db:
        store.db.execute('BEGIN IMMEDIATE');row=get(store,name)
        if row['version']!=version:raise ValueError('別の画面で変更されました。一覧を更新してください。')
        candidate=row['name'][:65]+' のコピー';n=2
        while store.db.execute('SELECT 1 FROM post_templates WHERE name=?',(candidate,)).fetchone():
            candidate=row['name'][:65]+' のコピー '+str(n);n+=1
        store.db.execute('INSERT INTO post_templates(name,body,group_name,image_id,updated_at) VALUES(?,?,?,?,?)',(candidate,row['body'],row['group_name'],row['image_id'],now().isoformat()))
    return get(store,candidate)
