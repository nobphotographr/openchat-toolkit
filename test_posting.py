import json
from datetime import datetime, timedelta
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from posting import Posts, ROOT, JST, effective_time, dispatch, validate, rooms

class FakeAdapter:
    def __init__(self,fail=None,confirmed=True):self.calls=[];self.fail=fail;self.confirmed=confirmed
    def step(self,action):
        self.calls.append(action)
        if self.fail==action:raise RuntimeError('lost reply')
    def preflight(self,*args):self.step('preflight')
    def prepare(self,*args):self.step('prepare')
    def verify_prepared(self,*args):self.step('verify')
    def invoke(self,*args):self.step('invoke')
    def confirm(self,*args):self.step('confirm');return self.confirmed
    def close(self):self.step('close')

class PostingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'state');self.path=Path(self.tmp.name)/'posts.sqlite3';self.store=Posts(self.path)
        self.current=datetime(2026,10,12,10,0,tzinfo=JST);self.room=rooms()[0]
        self.store.pause(False)  # Synthetic test store only.
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def scheduled(self,body='試験の本文'):
        row=self.store.save(self.room,body)
        return self.store.schedule(row['id'],row['version'],self.current,current=self.current)
    def test_draft_does_not_dispatch(self):
        self.store.save(self.room,'下書き');adapter=FakeAdapter();self.assertEqual(dispatch(self.store,adapter,self.current)['status'],'idle');self.assertEqual(adapter.calls,[])
    def test_duplicate_reservation_and_double_click_rejected(self):
        row=self.scheduled();other=self.store.save(self.room,row['body'])
        with self.assertRaises(ValueError):self.store.schedule(other['id'],other['version'],self.current,self.current)
        with self.assertRaises(ValueError):self.store.schedule(row['id'],row['version'],self.current,self.current)
        self.assertEqual(len([r for r in self.store.list() if r['status']=='scheduled']),1)
    def test_stale_editor_cannot_overwrite(self):
        row=self.store.save(self.room,'一');self.store.save(self.room,'二',row['id'],row['version'])
        with self.assertRaises(ValueError):self.store.save(self.room,'三',row['id'],row['version'])
    def test_reserved_body_is_immutable(self):
        row=self.scheduled()
        with self.assertRaises(ValueError):self.store.save(self.room,'差替え',row['id'],row['version'])
    def test_cancel_prevents_send(self):
        row=self.scheduled();self.store.change(row['id'],row['version'],'cancelled')
        a=FakeAdapter();dispatch(self.store,a,self.current);self.assertEqual(a.calls,[])
    def test_claim_and_cancel_are_mutually_exclusive(self):
        row=self.scheduled();self.store.claim(self.current)
        with self.assertRaises(ValueError):self.store.change(row['id'],row['version'],'cancelled')
    def test_two_connections_cannot_claim_twice(self):
        self.scheduled();other=Posts(self.path)
        try:self.assertIsNotNone(self.store.claim(self.current));self.assertIsNone(other.claim(self.current))
        finally:other.close()
    def test_pending_survives_restart(self):
        row=self.scheduled();self.store.close();self.store=Posts(self.path);self.store.recover()
        self.assertEqual(self.store.get(row['id'])['status'],'scheduled')
    def test_success_runs_only_once(self):
        row=self.scheduled();a=FakeAdapter();self.assertEqual(dispatch(self.store,a,self.current)['status'],'local_confirmed')
        self.assertEqual(a.calls.count('invoke'),1);dispatch(self.store,a,self.current);self.assertEqual(a.calls.count('invoke'),1)
        self.store.close();self.store=Posts(self.path);self.store.recover();dispatch(self.store,a,self.current);self.assertEqual(a.calls.count('invoke'),1)
    def test_send_intent_committed_before_invoke(self):
        row=self.scheduled();a=FakeAdapter()
        def invoke(*args):
            other=Posts(self.path)
            try:self.assertEqual(other.get(row['id'])['status'],'send_intent')
            finally:other.close()
            a.step('invoke')
        a.invoke=invoke;dispatch(self.store,a,self.current)
    def test_lost_send_reply_never_retries(self):
        row=self.scheduled();a=FakeAdapter(fail='invoke');self.assertEqual(dispatch(self.store,a,self.current)['status'],'needs_review')
        self.store.recover();dispatch(self.store,a,self.current);self.assertEqual(a.calls.count('invoke'),1)
        with self.assertRaises(ValueError):self.store.change(row['id'],self.store.get(row['id'])['version'],'draft')
    def test_uncertain_confirmation_never_retries(self):
        self.scheduled();a=FakeAdapter(confirmed=False);self.assertEqual(dispatch(self.store,a,self.current)['status'],'needs_review');dispatch(self.store,a,self.current);self.assertEqual(a.calls.count('invoke'),1)
    def test_preparation_failure_no_send(self):
        self.scheduled();a=FakeAdapter(fail='prepare');self.assertEqual(dispatch(self.store,a,self.current)['status'],'needs_review');self.assertNotIn('invoke',a.calls)
    def test_wrong_room_preflight_no_send(self):
        self.scheduled();a=FakeAdapter(fail='preflight');self.assertEqual(dispatch(self.store,a,self.current)['status'],'blocked');self.assertNotIn('prepare',a.calls)
    def test_crash_recovery_never_requeues(self):
        for phase in ('preparing','send_intent'):
            row=self.scheduled(phase);self.store.finish(row['id'],phase)
        self.store.close();self.store=Posts(self.path);self.store.recover()
        self.assertEqual({r['status'] for r in self.store.list()},{'needs_review'})
    def test_quiet_hours_and_expiry(self):
        self.scheduled();a=FakeAdapter();dispatch(self.store,a,self.current.replace(hour=21));self.assertEqual(a.calls,[])
        self.assertEqual(dispatch(self.store,a,self.current+timedelta(days=1,hours=1))['status'],'idle');self.assertEqual(self.store.list()[0]['status'],'expired')
    def test_next_morning_catchup(self):
        row=self.scheduled();a=FakeAdapter();self.assertEqual(dispatch(self.store,a,self.current+timedelta(hours=22))['status'],'local_confirmed')
    def test_pause_persists(self):
        self.scheduled();self.store.pause(True);self.store.close();self.store=Posts(self.path)
        a=FakeAdapter();dispatch(self.store,a,self.current);self.assertEqual(a.calls,[]);self.store.pause(False);dispatch(self.store,a,self.current);self.assertIn('invoke',a.calls)
    def test_content_tamper_blocks(self):
        row=self.scheduled()
        with self.store.db:self.store.db.execute('UPDATE posts SET body=? WHERE id=?',('改変',row['id']))
        a=FakeAdapter();dispatch(self.store,a,self.current);self.assertEqual(a.calls,[]);self.assertEqual(self.store.get(row['id'])['status'],'blocked')
    def test_review_closure_does_not_send(self):
        row=self.scheduled();dispatch(self.store,FakeAdapter(confirmed=False),self.current);row=self.store.get(row['id']);self.store.change(row['id'],row['version'],'review_closed');a=FakeAdapter();dispatch(self.store,a,self.current);self.assertEqual(a.calls,[])
    def test_time_boundary(self):
        self.assertEqual(effective_time(self.current.replace(hour=7)).hour,8)
        self.assertEqual(effective_time(self.current.replace(hour=8)).hour,8)
        self.assertEqual(effective_time(self.current.replace(hour=20,minute=59)).hour,20)
        next_day=effective_time(self.current.replace(hour=21));self.assertEqual(next_day.day,13);self.assertEqual(next_day.hour,8)
    def test_validation(self):
        for room,body in [('LINE','本文'),('未登録','本文'),(self.room,'  '),(self.room,'a\x00b'),(self.room,'🐰'*2001)]:
            with self.assertRaises(ValueError):validate(room,body)
        self.assertEqual(validate(self.room,'改行\r\n本文'),'改行\n本文')
    def test_template_only_is_not_job(self):
        self.store.template('案内','本文');self.assertEqual(self.store.list(),[])
    def test_blocked_can_return_to_draft(self):
        row=self.scheduled();dispatch(self.store,FakeAdapter(fail='preflight'),self.current);row=self.store.get(row['id']);self.store.change(row['id'],row['version'],'draft');self.assertIsNone(self.store.get(row['id'])['approval'])
    def test_locks_prevent_worker_ui_operations(self):
        import posting_worker
        from runtime_lock import Refused
        with patch.object(posting_worker,'WorkerLock',side_effect=Refused('worker_already_running')):
            with patch.object(posting_worker,'Posts') as database:
                self.assertEqual(posting_worker.run()['status'],'busy');database.assert_not_called()

if __name__=='__main__':unittest.main()
