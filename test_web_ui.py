import http.client
import json
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from posting import ROOT, Posts, now, rooms
from web_ui import make_server


class WebUITest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'state')
        self.server=make_server(Path(self.temp.name))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.origin=f'http://127.0.0.1:{self.server.server_port}'
        self.cookie=''

    def tearDown(self):
        self.server.shutdown();self.thread.join();self.server.server_close();self.temp.cleanup()

    def request(self, action, data=None, *, origin=None, cookie=True, host=None, content_type='application/json'):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        headers={'Origin':self.origin if origin is None else origin,'Content-Type':content_type}
        if cookie: headers['Cookie']=self.cookie
        if host: headers['Host']=host
        conn.request('POST','/api/'+action,json.dumps(data or {}),headers)
        response=conn.getresponse();body=json.loads(response.read())
        result=response.status,body,response.getheader('Set-Cookie')
        conn.close();return result

    def login(self):
        code,_,cookie=self.request('session',{'token':self.server.app.secret})
        self.assertEqual(code,200);self.cookie=cookie.split(';')[0]
        self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)

    def draft(self):
        self.login()
        code,row,_=self.request('save',{'room':rooms()[0],'body':'検証用\n<script>alert(1)</script>'})
        self.assertEqual(code,200);return row

    def preview(self,row):
        return self.request('preview',{'id':row['id'],'version':row['version'],'when':(now()+timedelta(days=2)).replace(hour=22).isoformat()})

    def test_requires_auth_and_csrf(self):
        self.assertEqual(self.request('overview',cookie=False)[0],401)
        self.assertEqual(self.request('session',{'token':'wrong'})[0],403)
        self.login()
        self.assertEqual(self.request('save',origin='https://evil.example')[0],403)
        self.assertEqual(self.request('overview',host='evil.example')[0],403)
        self.assertEqual(self.request('pause',content_type='text/plain')[0],403)

    def test_preview_does_not_schedule_and_confirmation_once(self):
        row=self.draft();code,preview,_=self.preview(row);self.assertEqual(code,200)
        self.assertEqual(preview['row']['due_at'][11:16],'08:00')
        self.assertEqual(self.request('overview')[1]['posts'][0]['status'],'draft')
        data={'confirmation':preview['confirmation']}
        self.assertEqual(self.request('schedule',data)[0],200)
        self.assertEqual(self.request('schedule',data)[0],400)
        self.assertEqual(len(self.request('overview')[1]['posts']),1)

    def test_changed_draft_invalidates_confirmation(self):
        row=self.draft();preview=self.preview(row)[1]
        self.assertEqual(self.request('save',{**row,'body':'changed'})[0],200)
        self.assertEqual(self.request('schedule',{'confirmation':preview['confirmation']})[0],400)
        self.assertEqual(self.request('overview')[1]['posts'][0]['status'],'draft')

    def test_expired_confirmation(self):
        row=self.draft();preview=self.preview(row)[1];key=preview['confirmation']
        old=self.server.app.approvals[key];self.server.app.approvals[key]=(0,*old[1:])
        self.assertEqual(self.request('schedule',{'confirmation':key})[0],400)

    def test_unknown_delivery_cannot_requeue(self):
        row=self.draft()
        p=Posts(Path(self.temp.name)/'posting.sqlite3');p.finish(row['id'],'needs_review');r=p.get(row['id']);p.close()
        self.assertEqual(self.request('change',dict(id=r['id'],version=r['version'],target='draft'))[0],400)
        self.assertEqual(self.request('change',dict(id=r['id'],version=r['version'],target='review_closed'))[0],200)

    def test_restart_preserves_data_and_invalidates_confirmation(self):
        row=self.draft();preview=self.preview(row)[1]
        from web_ui import Application
        self.server.app=Application(Path(self.temp.name));self.login()
        self.assertEqual(self.request('overview')[1]['posts'][0]['id'],row['id'])
        self.assertEqual(self.request('schedule',{'confirmation':preview['confirmation']})[0],400)

    def test_invalid_room_and_unknown_sender_api(self):
        self.login()
        self.assertEqual(self.request('save',dict(room='wrong',body='test'))[0],400)
        self.assertEqual(self.request('dispatch')[0],400)
        self.assertEqual(self.request('pause',dict(paused='false'))[0],400)

    def test_static_allowlist(self):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        for path,status in [('/',200),('/app.js',200),('/style.css',200),('/../state/posting.sqlite3',404),('/web_ui.py',404)]:
            conn.request('GET',path);response=conn.getresponse();self.assertEqual(response.status,status);response.read()
            self.assertIn("frame-ancestors 'none'",response.getheader('Content-Security-Policy'))
        conn.close()

    def test_archive_rules_and_status(self):
        self.login()
        self.assertEqual(self.request('archive',{'mode':'history'})[1]['count'],0)
        self.assertEqual(self.request('add_rule',{'term':'企画'})[0],200)
        self.assertEqual(self.request('rules')[1][0]['term'],'企画')
        self.assertEqual(self.request('archive',{'start':'wrong'})[0],400)
        self.assertGreater(len(self.request('status')[1]),0)

    def test_recurring_confirmation_uses_frozen_spec_and_single_use(self):
        row=self.draft()
        start=(now()+timedelta(days=2)).date().isoformat()
        spec=dict(weekdays=list(range(7)),time='10:00',start=start,end=start)
        code,preview,_=self.request('preview',dict(id=row['id'],version=row['version'],recurrence=spec))
        self.assertEqual(code,200);self.assertEqual(preview['recurrence'],spec)
        self.assertEqual(self.request('overview')[1]['recurrences'],[])
        code,rule,_=self.request('schedule',dict(confirmation=preview['confirmation'],recurrence={**spec,'time':'11:00'}))
        self.assertEqual(code,200)
        result=self.request('overview')[1]
        self.assertEqual(result['recurrences'][0]['spec']['time'],'10:00')
        self.assertEqual(len([p for p in result['posts'] if p['status']=='scheduled']),1)
        self.assertEqual(self.request('schedule',dict(confirmation=preview['confirmation']))[0],400)

    def test_operations_preview_is_isolated_and_read_only(self):
        self.login();code,result,_=self.request('operations')
        self.assertEqual(code,200);self.assertNotIn('note_jobs',result)
        self.assertFalse((Path(self.temp.name)/'note-state').exists())

    def test_status_shows_missing_today_and_saved_continuation(self):
        from collector import Archive
        import screen_resume
        self.login();code,rows,_=self.request('status');self.assertEqual(code,200)
        row=rows[0];self.assertEqual(row['today_status'],'本日未取得')
        a=Archive(Path(self.temp.name)/'history.sqlite3')
        try:
            screen_resume.initialize(a.db)
            with a.db:
                a.db.execute('INSERT INTO screen_resume_jobs VALUES (?,?,?,?,?,?,?,?,?,?)',(row['room'],'job','null','[{},{}]',now().isoformat(),4,'pending',now().isoformat(),0,0))
                a.db.execute('INSERT INTO attempts(room,started_at,status) VALUES (?,?,?)',(row['room'],now().isoformat(),'screen_pending'))
        finally:a.close()
        row=next(r for r in self.request('status')[1] if r['room']==row['room'])
        self.assertEqual(row['today_status'],'取得途中・続きの取得待ち')
        self.assertEqual(row['resume_progress']['buffered_texts'],2)

    def test_image_authentication_and_changed_attachment(self):
        import base64
        from test_media import png
        self.login()
        code,image,_=self.request('upload_image',dict(base64=base64.b64encode(png()).decode()))
        self.assertEqual(code,200)
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        conn.request('GET','/media/'+image['id']);res=conn.getresponse();self.assertEqual(res.status,404);res.read()
        conn.request('GET','/media/'+image['id'],headers={'Cookie':self.cookie});res=conn.getresponse();self.assertEqual(res.status,200);self.assertEqual(res.getheader('Content-Type'),'image/png');res.read();conn.close()
        code,row,_=self.request('save',dict(room=rooms()[0],body='',image_id=image['id']));self.assertEqual(code,200)
        code,preview,_=self.preview(row);self.assertEqual(code,200)
        self.assertEqual(self.request('save',{**row,'body':'変更後','image_id':''})[0],200)
        self.assertEqual(self.request('schedule',dict(confirmation=preview['confirmation']))[0],400)

    def test_template_library_end_to_end(self):
        self.login()
        code,row,_=self.request('template_save',dict(name='検証用',body='本文',group_name='テスト'));self.assertEqual(code,200)
        self.assertEqual(self.request('template_list')[1][0]['name'],'検証用')
        code,copy,_=self.request('template_duplicate',dict(name=row['name'],version=row['version']));self.assertEqual(code,200)
        self.assertNotEqual(copy['name'],row['name'])
        self.assertEqual(self.request('template_archive',dict(name=row['name'],version=row['version'],archived=True))[0],200)
        overview=self.request('overview')[1];self.assertEqual(len(overview['templates']),1)
        self.assertEqual(overview['posts'],[])


if __name__=='__main__':unittest.main()
