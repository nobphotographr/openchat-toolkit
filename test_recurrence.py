from datetime import datetime,timedelta
from pathlib import Path
import tempfile
import unittest
from posting import ROOT,Posts,JST,rooms,dispatch
import recurrence as r
from test_posting import FakeAdapter


class RecurrenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'state');self.path=Path(self.tmp.name)/'posts.sqlite3';self.store=Posts(self.path)
        self.current=datetime(2026,10,12,9,tzinfo=JST)
        self.spec=dict(weekdays=list(range(7)),time='10:00',start='2026-10-12',end='')
        self.store.pause(False)  # Synthetic test store only.
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def create(self,spec=None,body='繰り返しテスト'):
        row=self.store.save(rooms()[0],body)
        return r.create(self.store,row['id'],row['version'],spec or self.spec,self.current)
    def jobs(self):return [p for p in self.store.list() if p['status']=='scheduled']
    def test_daily_unique_and_restart(self):
        self.create();self.assertEqual(len(self.jobs()),14)
        r.materialize(self.store,self.current);self.assertEqual(len(self.jobs()),14)
        self.store.close();self.store=Posts(self.path);r.materialize(self.store,self.current)
        self.assertEqual(len(self.jobs()),14)
    def test_weekdays_end_and_first_time(self):
        self.create({**self.spec,'weekdays':[0,2],'end':'2026-10-16'})
        self.assertEqual(sorted(p['due_at'][:10] for p in self.jobs()),['2026-10-12','2026-10-14'])
    def test_past_date_not_created_and_previous_day_expires(self):
        self.create();later=self.current+timedelta(days=5)
        r.materialize(self.store,later)
        self.assertTrue(all(p['due_at'][:10]>='2026-10-17' for p in self.jobs()))
        self.assertEqual(len([p for p in self.store.list() if p['status']=='expired']),5)
    def test_pause_blocks_pending_and_resume_skips_missed(self):
        rule=self.create();r.change(self.store,rule['id'],rule['version'],'paused',self.current)
        self.assertIsNone(self.store.claim(self.current+timedelta(hours=2)))
        rule=r.get(self.store,rule['id']);r.change(self.store,rule['id'],rule['version'],'active',self.current+timedelta(hours=2))
        self.assertIsNone(self.store.claim(self.current+timedelta(hours=2)))
        self.assertTrue(all(p['due_at'][:10]>'2026-10-12' for p in self.jobs()))
    def test_cancel_all_future_preserves_existing_history(self):
        rule=self.create();result=dispatch(self.store,FakeAdapter(),self.current+timedelta(hours=1))
        self.assertEqual(result['status'],'local_confirmed')
        r.change(self.store,rule['id'],rule['version'],'cancelled',self.current)
        self.assertEqual(len(self.jobs()),0)
        self.assertEqual(self.store.get(result['id'])['status'],'local_confirmed')
    def test_uncertain_delivery_blocks_following_occurrences(self):
        rule=self.create();result=dispatch(self.store,FakeAdapter(confirmed=False),self.current+timedelta(hours=1))
        self.assertEqual(result['status'],'needs_review')
        self.assertIsNone(self.store.claim(self.current+timedelta(days=1,hours=1)))
        r.materialize(self.store,self.current);self.assertEqual(r.get(self.store,rule['id'])['status'],'needs_review')
    def test_blocked_child_must_be_resolved_before_resume(self):
        rule=self.create();result=dispatch(self.store,FakeAdapter(fail='preflight'),self.current+timedelta(hours=1))
        r.materialize(self.store,self.current);rule=r.get(self.store,rule['id'])
        with self.assertRaises(ValueError):r.change(self.store,rule['id'],rule['version'],'active',self.current)
        child=self.store.get(result['id']);self.store.change(child['id'],child['version'],'cancelled')
        r.change(self.store,rule['id'],rule['version'],'active',self.current)
    def test_tampering_rule_holds_already_materialized_jobs(self):
        rule=self.create()
        with self.store.db:self.store.db.execute("UPDATE recurring_rules SET body='tampered' WHERE id=?",(rule['id'],))
        self.assertIsNone(self.store.claim(self.current+timedelta(hours=1)))
        r.materialize(self.store,self.current);self.assertEqual(r.get(self.store,rule['id'])['status'],'needs_review')
    def test_collision_holds_rule_instead_of_second_delivery(self):
        row=self.store.save(rooms()[0],'繰り返しテスト');self.store.schedule(row['id'],row['version'],self.current+timedelta(hours=1),self.current)
        rule=self.create();self.assertEqual(rule['status'],'needs_review');self.assertEqual(len(self.jobs()),1)
    def test_duplicate_rule_stale_revision_and_date_validation(self):
        rule=self.create()
        with self.assertRaises(ValueError):self.create()
        with self.assertRaises(ValueError):r.change(self.store,rule['id'],0,'paused',self.current)
        for spec in [{**self.spec,'time':'21:00'},{**self.spec,'weekdays':[]},{**self.spec,'weekdays':[True]},{**self.spec,'end':'2026-01-01'}]:
            with self.assertRaises(ValueError):r.normalize(spec)
    def test_inflight_rule_cannot_be_cancelled(self):
        rule=self.create();self.store.claim(self.current+timedelta(hours=1))
        with self.assertRaises(ValueError):r.change(self.store,rule['id'],rule['version'],'cancelled',self.current)
    def test_single_occurrence_cancel_does_not_reappear(self):
        self.create();post=self.jobs()[0]
        self.store.change(post['id'],post['version'],'cancelled');r.materialize(self.store,self.current)
        self.assertEqual(len(self.jobs()),13)
        self.assertEqual(self.store.get(post['id'])['status'],'cancelled')

    def test_two_materializers_cannot_duplicate_a_date(self):
        from concurrent.futures import ThreadPoolExecutor
        self.create()
        def poll():
            store=Posts(self.path)
            try:r.materialize(store,self.current+timedelta(days=3))
            finally:store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            for future in [pool.submit(poll),pool.submit(poll)]:future.result()
        self.assertEqual(len(self.jobs()),14)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM recurring_occurrences').fetchone()[0],17)


if __name__=='__main__':unittest.main()
