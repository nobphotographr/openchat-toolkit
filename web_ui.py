"""Loopback-only PostLab UI. This process never invokes the LINE sender."""
import argparse
from contextlib import closing
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
from pathlib import Path
import secrets
import sqlite3
import subprocess
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler
from urllib.parse import urlsplit, parse_qs

from posting import Posts, ROOT, LABELS, rooms, effective_time, signature
from collector import Archive
import archive_features as features


class Application:
    def __init__(self, state=None):
        from settings import ensure_home
        ensure_home()
        self.state = Path(state or ROOT/'state')
        self.secret = secrets.token_urlsafe(32)
        self.sessions = set()
        self.approvals = {}
        self.lock = threading.Lock()
        self.last_seen = time.monotonic()
        self.version = hashlib.sha256(b''.join((ROOT/name).read_bytes() for name in ('web_ui.py','operations.py','posting.py','recurrence.py','media.py','templates.py','collection_view.py','collection_retry.py','web/app.js','web/style.css'))).hexdigest()

    def call(self, action, data):
        self.last_seen = time.monotonic()
        from setup_api import handle, ACTIONS
        if action in ACTIONS:
            if action in ('save_settings','collect') and self.state.resolve() != (ROOT/'state').resolve():
                raise ValueError('別の保存先のプレビューでは設定変更・LINE取得を実行できません。')
            return handle(self,action,data)
        if action in ('collection_health','collection_retry'):
            from collection_view import build
            import collection_retry
            config=json.loads((ROOT/'collector-config.json').read_text(encoding='utf-8-sig'))
            with closing(Archive(self.state/'history.sqlite3')) as archive:
                if action=='collection_retry':return collection_retry.request(archive.db,data['room'],config)
                return build(archive,config)
        if action == 'upload_image':
            from media import upload
            return upload(self.state,data['base64'])
        if action == 'operations':
            from operations import overview
            return overview(self.state)
        with closing(Posts(self.state/'posting.sqlite3')) as p:
            if action == 'overview':
                from recurrence import listing
                from templates import listing as template_listing
                heartbeat=None
                try:
                    status=json.loads((self.state/'posting-status.json').read_text(encoding='utf-8'))
                    heartbeat=status.get('observed_at')
                except (OSError,ValueError): pass
                return dict(posts=p.list(), paused=p.paused(), rooms=rooms(), labels=LABELS,
                            recurrences=listing(p),
                            heartbeat=heartbeat,
                            templates=template_listing(p,False))
            if action.startswith('template_'):
                import templates
                if action=='template_list':return templates.listing(p)
                if action=='template_save':return templates.save(p,**{k:v for k,v in data.items() if k in ('name','body','group_name','image_id','original','version')})
                if action=='template_archive':return templates.archive(p,data['name'],data['version'],data['archived'])
                if action=='template_duplicate':return templates.duplicate(p,data['name'],data['version'])
            if action == 'save':
                return p.save(data['room'], data['body'], data.get('id'), data.get('version'),data.get('image_id',''))
            if action == 'preview':
                row = p.get(data['id'])
                p.validate(row)
                if row['status'] != 'draft' or row['version'] != data['version']:
                    raise ValueError('下書きが変更されています。保存し直してください。')
                repeat=None;upcoming=[]
                if data.get('recurrence') is not None:
                    from recurrence import normalize,dates
                    repeat=normalize(data['recurrence'])
                    upcoming=[v.isoformat() for v in dates(repeat,datetime.now().astimezone())]
                    if not upcoming:raise ValueError('指定した期間に実行できる日がありません。')
                    due=datetime.fromisoformat(upcoming[0])
                else: due = effective_time(datetime.fromisoformat(data['when']))
                if due <= datetime.now(due.tzinfo):
                    raise ValueError('未来の日時を指定してください。')
                row['due_at'] = due.isoformat()
                token = secrets.token_urlsafe(24)
                with self.lock:
                    self.approvals = {k:v for k,v in self.approvals.items() if v[0] > time.monotonic()}
                    self.approvals[token] = (time.monotonic()+300, row['id'], row['version'], signature(row), row['due_at'],repeat)
                return dict(row=row, confirmation=token,recurrence=repeat,upcoming=upcoming[:5])
            if action == 'schedule':
                with self.lock:
                    approval = self.approvals.pop(data['confirmation'], None)
                if approval is None or approval[0] < time.monotonic():
                    raise ValueError('確認の有効期限が切れました。もう一度内容を確認してください。')
                _, ident, version, digest, due, repeat = approval
                row = p.get(ident)
                row['due_at'] = due
                if row['version'] != version or signature(row) != digest:
                    raise ValueError('確認後に下書きが変更されました。')
                if repeat is not None:
                    from recurrence import create
                    if datetime.fromisoformat(due)<=datetime.now().astimezone():raise ValueError('確認中に初回時刻を過ぎました。もう一度確認してください。')
                    rule=create(p,ident,version,repeat)
                    return dict(id=rule['id'],status=rule['status'])
                return p.schedule(ident, version, datetime.fromisoformat(due))
            if action == 'recurrence_change':
                from recurrence import change
                rule=change(p,data['id'],data['version'],data['target'])
                return dict(id=rule['id'],status=rule['status'])
            if action == 'change': return p.change(data['id'],data['version'],data['target']) or {}
            if action == 'pause':
                if type(data['paused']) is not bool: raise ValueError('invalid_pause')
                p.pause(data['paused']); return {}
            if action == 'template':
                p.template(data['name'],data['body']); return {}
        with closing(Archive(self.state/'history.sqlite3')) as archive:
            db = archive.db
            features.initialize(db)
            if action == 'archive':
                features.refresh(db)
                count, rows = features.search(db, **{k:v for k,v in data.items() if k in ('query','room','start','end','mode','offset')}, limit=100)
                for row in rows: row['links'] = features.links(row['text'])
                return dict(count=count, rows=rows)
            if action == 'bookmark': return dict(saved=features.toggle_bookmark(db,data))
            if action == 'read': features.mark_read(db,data); return {}
            if action == 'rules':
                return [dict(zip(('id','term','room','enabled'),r)) for r in db.execute('SELECT id,term,room,enabled FROM keyword_rules ORDER BY id')]
            if action == 'add_rule':
                if data.get('room') and data['room'] not in rooms(): raise ValueError('宛先が不正です。')
                features.add_rule(db,data['term'],data.get('room',''),data.get('include_history',False)); return {}
            if action == 'toggle_rule':
                with db: db.execute('UPDATE keyword_rules SET enabled=1-enabled WHERE id=?',(data['id'],))
                return {}
            if action == 'status':
                from collection_view import build
                config=json.loads((ROOT/'collector-config.json').read_text(encoding='utf-8-sig'))
                return build(archive,config)['rooms']
        raise ValueError('unknown_action')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # Never log tokens, chat text, or URLs.

    def respond(self, code, value, content_type='application/json; charset=utf-8', cookie=None):
        body = value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        if cookie: self.send_header('Set-Cookie',cookie)
        self.end_headers(); self.wfile.write(body)

    def valid_host(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

    def authorized(self):
        from http.cookies import SimpleCookie
        cookie = SimpleCookie()
        try: cookie.load(self.headers.get('Cookie',''))
        except Exception: return False
        token = cookie.get(f'postlab_{self.server.server_port}')
        return token is not None and token.value in self.server.app.sessions

    def do_GET(self):
        if not self.valid_host(): return self.respond(403,{'error':'接続先が不正です。'})
        path = urlsplit(self.path).path
        files = {'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
        files.update({'/app-icon.svg':('app-icon.svg','image/svg+xml'),
                      '/app-icon.ico':('app-icon.ico','image/x-icon'),
                      '/favicon.ico':('app-icon.ico','image/x-icon')})
        if path in files:
            name, mime = files[path]
            return self.respond(200,(ROOT/'web'/name).read_bytes(),mime)
        if path == '/api/ping' and self.authorized():
            self.server.app.last_seen=time.monotonic()
            return self.respond(200,{'ok':True,'version':self.server.app.version})
        if path.startswith('/media/') and self.authorized():
            from media import read
            try:return self.respond(200,read(self.server.app.state,path[7:]),'image/png')
            except ValueError:return self.respond(404,{'error':'画像を読み取れません。'})
        return self.respond(404,{'error':'見つかりません。'})

    def do_POST(self):
        origin = f'http://127.0.0.1:{self.server.server_port}'
        if not self.valid_host() or self.headers.get('Origin') != origin or self.headers.get('Content-Type','').split(';')[0] != 'application/json':
            return self.respond(403,{'error':'この画面から操作してください。'})
        try:
            size = int(self.headers.get('Content-Length','0'))
            maximum=14*1024*1024+100 if self.path in ('/api/upload_image','/api/import_mobile') and self.authorized() else 65536
            if not 0 < size <= maximum: raise ValueError('リクエストが大きすぎます。')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data,dict): raise ValueError('invalid_request')
            if self.path == '/api/session':
                if not secrets.compare_digest(str(data.get('token','')),self.server.app.secret):
                    return self.respond(403,{'error':'ランチャーから開き直してください。'})
                session = secrets.token_urlsafe(32)
                self.server.app.sessions.add(session)
                self.server.app.last_seen=time.monotonic()
                return self.respond(200,{},cookie=f'postlab_{self.server.server_port}={session}; HttpOnly; SameSite=Strict; Path=/')
            if not self.authorized(): return self.respond(401,{'error':'ランチャーから開き直してください。'})
            if not self.path.startswith('/api/'): return self.respond(404,{})
            result = self.server.app.call(self.path[5:],data)
            self.respond(200,result)
        except (ValueError, KeyError, TypeError, sqlite3.Error) as error:
            message = str(error) if isinstance(error,ValueError) else '操作できませんでした。入力または更新状態を確認してください。'
            self.respond(400,{'error':message})
        except Exception:
            self.respond(500,{'error':'処理に失敗しました。再読み込みして状態を確認してください。'})


def make_server(state=None):
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.app = Application(state)
    return server


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--state',type=Path)
    parser.add_argument('--serve-only',action='store_true')
    parser.add_argument('--browser',action='store_true',help='Open the same authenticated UI in a normal browser window with an address bar')
    args=parser.parse_args()
    root=Path(args.state or ROOT/'state')
    root.mkdir(parents=True,exist_ok=True)
    edge=Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
    def launch(port,token):
        url=f'http://127.0.0.1:{port}/#'+token
        view=['--new-window',url] if args.browser else ['--app='+url]
        subprocess.Popen([str(edge),*view,'--user-data-dir='+str(root/'web-profile'),'--no-first-run','--window-size=1400,940'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if not args.serve_only:
        try:
            previous=json.loads((root/'web-session.json').read_text(encoding='utf-8'))
            origin=f"http://127.0.0.1:{int(previous['port'])}"
            request=Request(origin+'/api/session',data=json.dumps({'token':previous['token']}).encode(),headers={'Origin':origin,'Content-Type':'application/json'})
            with build_opener(ProxyHandler({})).open(request,timeout=2) as response:
                if response.status==200:
                    cookie=response.headers.get('Set-Cookie','').split(';')[0]
                    ping=Request(origin+'/api/ping',headers={'Cookie':cookie})
                    with build_opener(ProxyHandler({})).open(ping,timeout=2) as check:
                        if json.load(check).get('version')==Application(root).version:
                            launch(previous['port'],previous['token']);return
        except (OSError,ValueError,KeyError): pass
    server=make_server(args.state)
    # This ignored local file allows test tooling to connect; never print its token.
    (root/'web-session.json').write_text(json.dumps({'port':server.server_port,'token':server.app.secret}),encoding='utf-8')
    if not args.serve_only:
        launch(server.server_port,server.app.secret)
    def idle():
        while time.monotonic()-server.app.last_seen < 1200:
            time.sleep(20)
        server.shutdown()
    threading.Thread(target=idle,daemon=True).start()
    try: server.serve_forever()
    finally: server.server_close()


if __name__ == '__main__': main()
