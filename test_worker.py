from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from collector import Archive, ROOT
import worker

ROOM = 'サンプルルーム'
RAW = '2026.10.12 月曜日\n12:00 名前 本文\n'.encode()


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'state')
        self.root = Path(self.tmp.name)
        (self.root/'collector-config.json').write_bytes((ROOT/'collector-config.json').read_bytes())
        self.patch = patch.object(worker, 'ROOT', self.root)
        self.patch.start()
        self.archive = Archive(self.root/'db.sqlite3')
    def tearDown(self):
        self.archive.close(); self.patch.stop(); self.tmp.cleanup()
    def runner(self, args, **kwargs):
        path = Path(args[-1]); path.write_bytes(RAW)
        reply = {'status':'exported','room':ROOM,'path':str(path),'send_invocations':0}
        return subprocess.CompletedProcess(args, 0, json.dumps(reply).encode(), b'')
    def test_repeated_real_process_equivalent(self):
        self.assertEqual(worker.collect(self.archive,ROOM,self.runner)['new_records'],1)
        self.archive.close(); self.archive=Archive(self.root/'db.sqlite3')
        self.assertEqual(worker.collect(self.archive,ROOM,self.runner)['new_records'],0)
        self.assertEqual(list((self.root/'state/exports').glob('*.txt')),[])
        self.assertEqual(self.archive.db.execute('SELECT raw FROM snapshots').fetchone()[0],RAW)
    def test_timeout_not_success(self):
        def timed(*args,**kw):raise subprocess.TimeoutExpired('native',50)
        result=worker.collect(self.archive,ROOM,timed)
        self.assertEqual(result['status'],'stopped')
        self.assertEqual(self.archive.status(),[])
    def test_wrong_room_receipt_keeps_untrusted_file(self):
        def wrong(args,**kw):
            p=self.runner(args,**kw);r=json.loads(p.stdout);r['room']='wrong';p.stdout=json.dumps(r).encode();return p
        self.assertEqual(worker.collect(self.archive,ROOM,wrong)['reason'],'native_result_identity_mismatch')
        self.assertEqual(self.archive.status(),[])
        self.assertEqual(len(list((self.root/'state/exports').glob('*.txt'))),1)
    def test_wrong_path_receipt(self):
        def wrong(args,**kw):
            p=self.runner(args,**kw);r=json.loads(p.stdout);r['path']=str(self.root/'unexpected.txt');p.stdout=json.dumps(r).encode();return p
        self.assertEqual(worker.collect(self.archive,ROOM,wrong)['reason'],'native_result_identity_mismatch')
    def test_nonzero_send_receipt(self):
        def wrong(args,**kw):
            p=self.runner(args,**kw);r=json.loads(p.stdout);r['send_invocations']=1;p.stdout=json.dumps(r).encode();return p
        self.assertEqual(worker.collect(self.archive,ROOM,wrong)['status'],'stopped')
    def test_native_failure_not_ingested(self):
        def fail(args,**kw):return subprocess.CompletedProcess(args,2,b'{"status":"stopped","reason":"line_download_failed"}',b'')
        self.assertEqual(worker.collect(self.archive,ROOM,fail)['reason'],'line_download_failed')
        self.assertEqual(self.archive.status(),[])
    def test_invalid_native_json(self):
        def fail(args,**kw):return subprocess.CompletedProcess(args,0,b'',b'')
        self.assertEqual(worker.collect(self.archive,ROOM,fail)['reason'],'native_result_unreadable')
    def test_interrupted_attempt_preserved(self):
        with self.archive.db:self.archive.db.execute("INSERT INTO attempts(room,started_at,status) VALUES (?,?,'running')",(ROOM,worker.now()))
        worker.collect(self.archive,ROOM,self.runner)
        self.assertEqual(self.archive.db.execute('SELECT status FROM attempts ORDER BY id').fetchall(),[('interrupted',),('imported',)])
    def test_due_round_robin_and_test_excluded(self):
        current=datetime.now(timezone.utc)
        config={'interval_seconds':3600,'rooms':[{'name':ROOM,'enabled':True,'test':True},
            {'name':'別のサンプルルーム','enabled':True},{'name':'もう一つのサンプルルーム','enabled':True}]}
        self.assertEqual(worker.select_due(self.archive,config,current),'別のサンプルルーム')
        with self.archive.db:self.archive.db.execute("INSERT INTO attempts(room,started_at,status) VALUES (?,?,'stopped')",('別のサンプルルーム',current.isoformat()))
        self.assertEqual(worker.select_due(self.archive,config,current),'もう一つのサンプルルーム')
        self.assertEqual(worker.select_due(self.archive,config,current+timedelta(hours=1)),'別のサンプルルーム')
    def test_invalid_export_retains_source_file(self):
        def malformed(args,**kw):
            p=self.runner(args,**kw);Path(args[-1]).write_bytes(b'invalid');return p
        self.assertEqual(worker.collect(self.archive,ROOM,malformed)['status'],'stopped')
        self.assertEqual(len(list((self.root/'state/exports').glob('*.txt'))),1)
    def test_posting_lock_excludes_collector(self):
        from runtime_lock import WorkerLock, Refused
        lock=self.root/'production-worker'
        with WorkerLock(lock):
            with self.assertRaises(Refused):
                with WorkerLock(lock): self.fail('concurrent access')
        with WorkerLock(lock): pass
    def test_no_due_room(self):
        config={'interval_seconds':3600,'rooms':[{'name':'別のサンプルルーム','enabled':False}]}
        self.assertIsNone(worker.select_due(self.archive,config,datetime.now(timezone.utc)))
    def nightly_config(self):
        return {'daily_schedule':{'timezone':'Asia/Tokyo','time':'03:00','window_minutes':60},
                'rooms':[{'name':'別のサンプルルーム','enabled':True},{'name':'もう一つのサンプルルーム','enabled':True},
                         {'name':ROOM,'enabled':True,'test':True},{'name':'disabled','enabled':False}]}

    def test_nightly_start_uses_jst_with_daytime_catchup(self):
        config=self.nightly_config()
        for hour,minute in [(0,0),(2,59)]:
            local=datetime(2026,10,13,hour,minute,tzinfo=timezone(timedelta(hours=9)))
            self.assertIsNone(worker.select_due(self.archive,config,local.astimezone(timezone.utc)))
        for hour,minute in [(3,0),(3,59),(4,0),(12,0),(23,59)]:
            local=datetime(2026,10,13,hour,minute,tzinfo=timezone(timedelta(hours=9)))
            self.assertEqual(worker.select_due(self.archive,config,local.astimezone(timezone.utc)),'別のサンプルルーム')

    def test_nightly_round_once_per_room_survives_restart_and_failure(self):
        config=self.nightly_config();start=datetime(2026,10,13,3,tzinfo=timezone(timedelta(hours=9)))
        for room,status in [('別のサンプルルーム','stopped'),('もう一つのサンプルルーム','imported')]:
            self.assertEqual(worker.select_due(self.archive,config,start),room)
            with self.archive.db:
                self.archive.db.execute('INSERT INTO attempts(room,started_at,status) VALUES (?,?,?)',(room,start.astimezone(timezone.utc).isoformat(),status))
        self.archive.close();self.archive=Archive(self.root/'db.sqlite3')
        self.assertIsNone(worker.select_due(self.archive,config,start+timedelta(minutes=30)))
        self.assertEqual(worker.select_due(self.archive,config,start+timedelta(days=1)),'別のサンプルルーム')

    def test_nightly_catches_missed_night_after_unlock_in_daytime(self):
        config=self.nightly_config();current=datetime(2026,10,13,18,tzinfo=timezone(timedelta(hours=9)))
        self.assertEqual(worker.select_due(self.archive,config,current),'別のサンプルルーム')
        self.assertEqual(worker.select_due(self.archive,config,current.replace(day=14,hour=3)),'別のサンプルルーム')
    def test_nonzero_exit_with_success_payload_rejected(self):
        def failed(args,**kw):
            p=self.runner(args,**kw);p.returncode=2;return p
        self.assertEqual(worker.collect(self.archive,ROOM,failed)['status'],'stopped')
        self.assertEqual(self.archive.status(),[])
    def test_cleanup_failure_does_not_discard_successful_export(self):
        def kept(args,**kw):
            p=self.runner(args,**kw)
            p.stderr=b'{"kind":"window_cleanup","status":"preserved","reason":"existing_draft_preserved"}\n'
            return p
        result=worker.collect(self.archive,ROOM,kept)
        self.assertEqual(result['status'],'imported')
        self.assertEqual(result['window_cleanup']['status'],'preserved')
        self.assertEqual(self.archive.status()[0]['records'],1)
    def test_timeout_uses_close_only_and_does_not_retry_export(self):
        calls=[]
        def timeout_then_close(args,**kw):
            calls.append(args[1])
            if args[1]=='--export':raise subprocess.TimeoutExpired('native',50)
            return subprocess.CompletedProcess(args,0,b'{"status":"closed"}',b'')
        result=worker.collect(self.archive,ROOM,timeout_then_close)
        self.assertEqual(calls,['--export','--close'])
        self.assertEqual(result['status'],'stopped')
        self.assertEqual(result['window_cleanup']['status'],'closed')
        self.assertEqual(self.archive.status(),[])
    def test_monitoring_target_without_pc_download_never_opens_line(self):
        config=json.loads((self.root/'collector-config.json').read_text(encoding='utf-8'))
        target=next(r for r in config['rooms'] if r['name']==ROOM)
        target.update(enabled=True,pc_history_download=False)
        (self.root/'collector-config.json').write_text(json.dumps(config),encoding='utf-8')
        def forbidden(*args,**kwargs):self.fail('PC download attempted')
        result=worker.collect(self.archive,ROOM,forbidden)
        self.assertEqual(result['status'],'awaiting_screen_reader')
        self.assertEqual(self.archive.status(),[])

    def enable_screen(self):
        config=json.loads((self.root/'collector-config.json').read_text(encoding='utf-8'))
        next(r for r in config['rooms'] if r['name']==ROOM).update(collection_method='screen_copy',pc_history_download=False)
        (self.root/'collector-config.json').write_text(json.dumps(config),encoding='utf-8')

    def test_screen_route_never_exports_and_reuses_checkpoint(self):
        from screen_archive import digest
        self.enable_screen();calls=[]
        def screen(args,**kwargs):
            calls.append(args)
            self.assertEqual(args[1],'--read-screen')
            payload={'schema_version':1,'status':'screen_read','room':ROOM,'observed_at':datetime.now(timezone.utc).isoformat(),
                     'bottom_confirmed':True,'send_invocations':0,'history_download_invocations':0,'pages':[{'messages':[{'body':b,'sha256':digest(b)} for b in ['new','middle','old']]}]}
            return subprocess.CompletedProcess(args,0,json.dumps(payload).encode(),b'{"kind":"window_cleanup","status":"closed"}')
        self.assertEqual(worker.collect(self.archive,ROOM,screen)['new_records'],3)
        self.assertEqual(worker.collect(self.archive,ROOM,screen)['new_records'],0)
        self.assertEqual(calls[1][-1],','.join(digest(b) for b in ['new','middle','old']))
        self.assertEqual(self.archive.db.execute('SELECT COUNT(*) FROM exports').fetchone()[0],0)

    def test_screen_timeout_closes_without_export_fallback(self):
        self.enable_screen();calls=[]
        def run(args,**kwargs):
            calls.append(args[1])
            if args[1]=='--read-screen':raise subprocess.TimeoutExpired('native',60)
            return subprocess.CompletedProcess(args,0,b'{"status":"closed"}',b'')
        result=worker.collect(self.archive,ROOM,run)
        self.assertEqual(calls,['--read-screen','--close'])
        self.assertEqual(result['status'],'stopped')

    def test_screen_wrong_room_payload_stops(self):
        self.enable_screen()
        def run(args,**kwargs):return subprocess.CompletedProcess(args,0,b'{"status":"screen_read","room":"wrong"}',b'')
        self.assertEqual(worker.collect(self.archive,ROOM,run)['reason'],'screen_room_mismatch')

    def test_resume_worker_passes_saved_cursor_and_uses_no_export(self):
        import screen_archive,screen_resume
        self.enable_screen()
        t=datetime.now(timezone.utc)-timedelta(seconds=30)
        def payload(bodies,start=0,step=4,more=True):
            nonlocal t
            t+=timedelta(seconds=1)
            return dict(schema_version=1,status='screen_read',room=ROOM,observed_at=t.isoformat(),bottom_confirmed=True,send_invocations=0,history_download_invocations=0,
                        pages=[dict(messages=[dict(body=b,sha256=screen_archive.digest(b)) for b in bodies])],start_scroll_steps=start,next_scroll_steps=step,has_more=more)
        screen_archive.ingest(self.archive,ROOM,payload(['old1','old2','old3']))
        screen_resume.ingest(self.archive,ROOM,payload(['n6','n5','n4','n3','n2','n1']))
        calls=[]
        def run(args,**kwargs):
            calls.append(args);self.assertEqual(args[1],'--read-screen');self.assertEqual(args[-1],'4')
            reply=payload(['n3','n2','n1','old1','old2','old3'],4,8,False)
            return subprocess.CompletedProcess(args,0,json.dumps(reply).encode(),b'{"kind":"window_cleanup","status":"closed"}')
        self.assertEqual(worker.collect(self.archive,ROOM,run)['new_records'],6)
        self.assertEqual(len(calls),1)

    def test_locked_session_is_deferred_without_consuming_attempt(self):
        import contextlib,io
        with patch('sys.argv',['worker.py','--room',ROOM]),patch.object(worker,'desktop_lock_path',return_value=self.root/'desktop'),patch('session_probe.probe',return_value=dict(ready=False,reason='input_desktop_unavailable')),patch.object(worker,'collect') as collect,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(worker.main(),0);collect.assert_not_called()
        import sqlite3
        with contextlib.closing(sqlite3.connect(self.root/'state/history.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],0)

    def fallback_runner(self, calls, failure=None, screen_bodies=None):
        from screen_archive import digest
        def run(args, **kwargs):
            calls.append(args[1])
            if args[1]=='--export':
                reply=dict(status='stopped',room=ROOM,reason='line_download_failed',send_invocations=0)
                if failure: reply.update(failure)
                return subprocess.CompletedProcess(args,2,json.dumps(reply).encode(),b'{"kind":"window_cleanup","status":"closed"}')
            self.assertEqual(args[1],'--read-screen')
            reply=dict(schema_version=1,status='screen_read',room=ROOM,observed_at=datetime.now(timezone.utc).isoformat(),bottom_confirmed=True,send_invocations=0,history_download_invocations=0,
                       pages=[{'messages':[dict(body=b,sha256=digest(b)) for b in (screen_bodies or ['new','middle','old'])]}])
            return subprocess.CompletedProcess(args,0,json.dumps(reply).encode(),b'{"kind":"window_cleanup","status":"closed"}')
        return run

    def test_confirmed_download_failure_switches_persistently_and_keeps_history(self):
        self.archive.ingest(ROOM,RAW,expected_room=ROOM)
        calls=[]; run=self.fallback_runner(calls)
        first=worker.collect(self.archive,ROOM,run)
        self.assertEqual(first['status'],'screen_baseline')
        self.assertEqual(first['fallback_reason'],'line_download_failed')
        self.assertEqual(calls,['--export','--read-screen'])
        self.assertEqual(self.archive.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0],1)
        self.assertEqual(self.archive.db.execute('SELECT COUNT(*) FROM records').fetchone()[0],4)
        self.archive.close(); self.archive=Archive(self.root/'db.sqlite3')
        self.assertEqual(worker.collect(self.archive,ROOM,run)['new_records'],0)
        self.assertEqual(calls,['--export','--read-screen','--read-screen'])

    def test_unknown_or_unsafe_failures_never_fallback(self):
        for failure in [dict(reason='foreground_changed'),dict(reason='save_dialog_missing_or_download_failed'),dict(room='wrong'),dict(send_invocations=1),dict(send_invocations=False)]:
            with self.subTest(failure=failure):
                calls=[]
                self.assertEqual(worker.collect(self.archive,ROOM,self.fallback_runner(calls,failure))['status'],'stopped')
                self.assertEqual(calls,['--export'])
        self.assertEqual(self.archive.db.execute('SELECT COUNT(*) FROM collection_routes').fetchone()[0],0)

    def test_preserved_dialog_blocks_fallback(self):
        calls=[]; run=self.fallback_runner(calls)
        def preserved(args,**kwargs):
            p=run(args,**kwargs);p.stderr=b'{"kind":"window_cleanup","status":"preserved"}';return p
        self.assertEqual(worker.collect(self.archive,ROOM,preserved)['status'],'stopped')
        self.assertEqual(calls,['--export'])

    def test_fallback_screen_failure_keeps_route_and_does_not_advance_checkpoint(self):
        calls=[]; run=self.fallback_runner(calls)
        def wrong(args,**kwargs):
            p=run(args,**kwargs)
            if args[1]=='--read-screen':
                reply=json.loads(p.stdout);reply['room']='wrong';p.stdout=json.dumps(reply).encode()
            return p
        self.assertEqual(worker.collect(self.archive,ROOM,wrong)['reason'],'screen_room_mismatch')
        self.assertEqual(self.archive.db.execute('SELECT COUNT(*) FROM screen_checkpoints').fetchone()[0],0)
        calls.clear()
        self.assertEqual(worker.collect(self.archive,ROOM,run)['status'],'screen_baseline')
        self.assertEqual(calls,['--read-screen'])

    def test_fallback_missing_anchor_retains_previous_records(self):
        calls=[];worker.collect(self.archive,ROOM,self.fallback_runner(calls))
        result=worker.collect(self.archive,ROOM,self.fallback_runner(calls,screen_bodies=['different']))
        self.assertEqual(result['status'],'screen_gap')
        self.assertEqual(result['new_records'],0)
        self.assertEqual(self.archive.db.execute('SELECT COUNT(*) FROM records').fetchone()[0],3)


if __name__=='__main__':unittest.main()
