from datetime import datetime,timedelta,timezone
from pathlib import Path
import json,sqlite3,tempfile,unittest
from unittest.mock import patch
from collector import Archive,ROOT,InvalidExport
import screen_archive as screen
import screen_resume as resume
from collection_schedule import select_due,JST


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'state');self.path=Path(self.tmp.name)/'db.sqlite3'
        self.a=Archive(self.path);self.rooms=patch('screen_archive.configured_rooms',return_value=['room']);self.rooms.start()
        self.t=datetime.now(timezone.utc)-timedelta(seconds=90)
        screen.ingest(self.a,'room',self.payload(['old1','old2','old3']))
    def tearDown(self):self.a.close();self.rooms.stop();self.tmp.cleanup()
    def payload(self,bodies,start=0,step=4,more=True):
        self.t+=timedelta(seconds=1)
        return dict(schema_version=1,status='screen_read',room='room',observed_at=self.t.isoformat(),bottom_confirmed=True,send_invocations=0,history_download_invocations=0,
                    pages=[dict(messages=[dict(body=b,sha256=screen.digest(b)) for b in bodies])],start_scroll_steps=start,next_scroll_steps=step,has_more=more)
    def take(self,bodies,start=0,step=4,more=True):return resume.ingest(self.a,'room',self.payload(bodies,start,step,more),start)
    def count(self):return self.a.db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
    def begin(self):
        self.assertEqual(self.take(['n6','n5','n4','n3','n2','n1'])['status'],'screen_pending')
        self.assertEqual(self.count(),3)
    def test_restart_resume_and_atomic_dedup(self):
        self.begin();self.a.close();self.a=Archive(self.path)
        self.assertEqual(resume.request(self.a.db,'room'),4)
        result=self.take(['n3','n2','n1','old1','old2','old3'],4,8,False)
        self.assertEqual(result['new_records'],6);self.assertEqual(self.count(),9)
        self.assertIsNone(resume.job(self.a.db,'room'))
        self.assertEqual(self.take(['n6','n5','n4','n3','n2','n1'],more=False)['new_records'],0)
    def test_multiple_chunks_and_new_identical_text(self):
        self.begin()
        self.assertEqual(self.take(['n3','n2','n1','n1','b','c'],4,8)['status'],'screen_pending')
        self.assertEqual(self.take(['n1','b','c','old1','old2','old3'],8,12,False)['new_records'],9)
        self.assertEqual(self.count(),12)
    def test_missing_overlap_rewinds_without_appending_or_checkpoint_advance(self):
        self.begin();before=resume.checkpoint(self.a.db,'room')
        result=self.take(['unrelated1','unrelated2','unrelated3'],4,8)
        self.assertEqual(result['status'],'screen_pending');self.assertEqual(result['next_scroll_steps'],0)
        self.assertEqual(result['buffered_texts'],6);self.assertEqual(resume.checkpoint(self.a.db,'room'),before)
        self.assertEqual(self.take(['n3','n2','n1','old1','old2','old3'],0,4,False)['new_records'],6)
    def test_many_new_arrivals_seek_forward_without_appending_disconnected_text(self):
        self.begin()
        self.take(['later1','later2','later3'],4,8)
        self.assertEqual(resume.request(self.a.db,'room'),0)
        self.take(['later4','later5','later6'],0,4)
        self.assertEqual(resume.request(self.a.db,'room'),4)
        self.assertEqual(self.take(['n3','n2','n1','old1','old2','old3'],4,8,False)['new_records'],6)
        self.assertEqual(self.count(),9)
    def test_ambiguous_overlap_never_commits(self):
        self.begin()
        result=self.take(['n3','n2','n1','n3','n2','n1','old1','old2','old3'],4,8)
        self.assertEqual(result['new_records'],0);self.assertEqual(self.count(),3)
    def test_read_failure_preserves_buffer_and_is_review(self):
        self.begin()
        for i in range(3):
            p=self.payload(['n3','n2','n1','old1','old2','old3'],4,8,False);p['skip_reasons']={'copy_not_observed':1}
            result=resume.ingest(self.a,'room',p,4)
            if i<2:self.assertEqual(result['status'],'screen_pending')
        self.assertEqual(result['status'],'screen_resume_review');self.assertEqual(self.count(),3)
        with self.assertRaises(InvalidExport):resume.request(self.a.db,'room')
    def test_confirmed_video_does_not_block_contiguous_text_resume(self):
        self.begin()
        p=self.payload(['n3','n2','n1','old1','old2','old3'],4,8,False)
        p['skip_reasons']={'video_attachment':2}
        result=resume.ingest(self.a,'room',p,4)
        self.assertEqual(result['new_records'],6)
        self.assertIsNone(resume.job(self.a.db,'room'))
        self.assertEqual(self.take(['n6','n5','n4','n3','n2','n1'],more=False)['new_records'],0)
    def test_video_plus_unreadable_text_still_preserves_buffer(self):
        self.begin();before=resume.checkpoint(self.a.db,'room')
        p=self.payload(['n3','n2','n1','old1','old2','old3'],4,8,False)
        p['skip_reasons']={'video_attachment':1,'copy_visual_label_not_unique_0':1}
        result=resume.ingest(self.a,'room',p,4)
        self.assertEqual(result['new_records'],0)
        self.assertEqual(result['buffered_texts'],6)
        self.assertEqual(resume.checkpoint(self.a.db,'room'),before)
    def test_repeated_short_text_uses_unique_longer_suffix(self):
        self.take(['unique','repeat','repeat','repeat','repeat'])
        result=self.take(['unique','repeat','repeat','repeat','repeat','x','repeat','repeat','repeat','old1','old2','old3'],4,8,False)
        self.assertEqual(result['new_records'],9)
        self.assertIsNone(resume.job(self.a.db,'room'))
    def test_wrong_room_cursor_or_old_response_rejected(self):
        self.begin()
        p=self.payload(['n3','n2','n1'],4,8)
        for field,value in [('room','other'),('start_scroll_steps',3),('next_scroll_steps',150),('has_more',1)]:
            q=dict(p);q[field]=value
            with self.subTest(field=field),self.assertRaises(InvalidExport):resume.ingest(self.a,'room',q,4)
        q=dict(p);q['observed_at']=(self.t-timedelta(seconds=10)).isoformat()
        with self.assertRaises(InvalidExport):resume.ingest(self.a,'room',q,4)
        self.assertEqual(self.count(),3)
    def test_checkpoint_change_is_not_overwritten(self):
        self.begin()
        with self.a.db:self.a.db.execute('UPDATE screen_checkpoints SET sequence=sequence+1')
        with self.assertRaises(InvalidExport):resume.request(self.a.db,'room')
        with self.assertRaises(InvalidExport):self.take(['n3','n2','n1','old1','old2','old3'],4,8,False)
    def test_database_failure_keeps_chunks_and_restart_can_finish_without_line(self):
        self.begin()
        self.a.db.execute("CREATE TRIGGER simulate_failure BEFORE INSERT ON records BEGIN SELECT RAISE(ABORT,'simulate'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.take(['n3','n2','n1','old1','old2','old3'],4,8,False)
        self.assertEqual(self.count(),3);self.assertIsNotNone(resume.job(self.a.db,'room'))
        self.a.db.execute('DROP TRIGGER simulate_failure');self.a.close();self.a=Archive(self.path)
        self.assertEqual(resume.finish_ready(self.a,'room')['new_records'],6)
        self.assertIsNone(resume.finish_ready(self.a,'room'))
    def test_preserves_each_chunks_observation_time(self):
        self.begin();first=self.t.astimezone(JST).isoformat()
        self.take(['n3','n2','n1','x','old1','old2','old3'],4,8,False)
        stamps=dict(self.a.db.execute("SELECT raw_text,timestamp FROM records WHERE raw_text IN ('n6','x')"))
        self.assertEqual(stamps['n6'],first);self.assertGreater(stamps['x'],first)
    def test_previous_day_buffer_completion_requests_a_fresh_scan(self):
        self.begin()
        previous=(self.t-timedelta(days=1)).isoformat()
        with self.a.db:self.a.db.execute('UPDATE screen_resume_jobs SET observed_at=?',(previous,))
        result=self.take(['n3','n2','n1','old1','old2','old3'],4,8,False)
        self.assertEqual(result['reason'],'screen_resume_previous_day_completed')
        self.assertEqual(result['new_records'],6)
        self.assertIsNone(resume.job(self.a.db,'room'))
    def test_end_without_anchor_retains_all_history(self):
        self.begin();result=self.take(['n3','n2','n1','different'],4,8,False)
        self.assertEqual(result['status'],'screen_resume_review');self.assertEqual(self.count(),3)
    def test_disjoint_pages_do_not_silently_commit(self):
        p=self.payload(['new1','new2','new3'])
        p['pages'].append(self.payload(['old1','old2','old3'])['pages'][0])
        for n,page in enumerate(p['pages']):
            for i,m in enumerate(page['messages']):m['item_id']=f'{n}-{i}'
        self.assertEqual(resume.ingest(self.a,'room',p)['status'],'screen_gap')
        self.assertEqual(self.count(),3)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'state');self.a=Archive(Path(self.tmp.name)/'db.sqlite3')
        self.config=dict(daily_schedule=dict(timezone='Asia/Tokyo',time='03:00',window_minutes=60),rooms=[dict(name='a',enabled=True),dict(name='b',enabled=True)])
        self.now=datetime(2026,10,15,10,tzinfo=JST)
    def tearDown(self):self.a.close();self.tmp.cleanup()
    def add(self,room,status='imported',stamp=None,reason=None):
        with self.a.db:self.a.db.execute('INSERT INTO attempts(room,started_at,status,reason) VALUES (?,?,?,?)',(room,(stamp or self.now).astimezone(timezone.utc).isoformat(),status,reason))
    def test_wake_catches_only_missing_rooms(self):
        self.assertEqual(select_due(self.a,self.config,self.now),'a');self.add('a')
        self.assertEqual(select_due(self.a,self.config,self.now),'b');self.add('b')
        self.assertIsNone(select_due(self.a,self.config,self.now+timedelta(hours=5)))
    def test_before_start_and_multiple_days_off(self):
        self.assertIsNone(select_due(self.a,self.config,self.now.replace(hour=2)))
        self.add('a',stamp=self.now-timedelta(days=5))
        self.assertEqual(select_due(self.a,self.config,self.now),'a')
        self.add('a');self.add('b');self.assertIsNone(select_due(self.a,self.config,self.now))
    def test_pending_continuation_waits_fairly_and_backs_off(self):
        self.add('a','screen_pending',self.now-timedelta(minutes=6))
        self.assertEqual(select_due(self.a,self.config,self.now),'b');self.add('b')
        self.assertEqual(select_due(self.a,self.config,self.now),'a');self.add('a','screen_pending')
        self.assertIsNone(select_due(self.a,self.config,self.now+timedelta(minutes=4)))
        self.assertEqual(select_due(self.a,self.config,self.now+timedelta(minutes=5)),'a')
    def test_interrupted_and_locked_retry_unknown_error_does_not(self):
        self.add('a','interrupted',self.now-timedelta(minutes=10));self.add('b','stopped',reason='wrong_room')
        self.assertEqual(select_due(self.a,self.config,self.now),'a')
        self.add('a','stopped',self.now-timedelta(minutes=6),'input_desktop_unavailable')
        self.assertEqual(select_due(self.a,self.config,self.now),'a')
        self.add('a','screen_resume_review');self.assertIsNone(select_due(self.a,self.config,self.now))
    def test_continuations_are_bounded(self):
        for i in range(12):self.add('a','screen_pending',self.now-timedelta(minutes=120-i*6))
        self.add('b');self.assertIsNone(select_due(self.a,self.config,self.now))
    def test_jst_date_boundary(self):
        self.add('a');self.add('b')
        self.assertIsNone(select_due(self.a,self.config,self.now.astimezone(timezone.utc)))
        self.assertIsNone(select_due(self.a,self.config,(self.now+timedelta(days=1)).replace(hour=2)))
        self.assertEqual(select_due(self.a,self.config,(self.now+timedelta(days=1)).replace(hour=3)),'a')
    def test_previous_days_resume_does_not_consume_todays_new_scan(self):
        self.add('a','screen_imported',self.now-timedelta(minutes=6),'screen_resume_previous_day_completed');self.add('b')
        self.assertEqual(select_due(self.a,self.config,self.now),'a')
        self.add('a','screen_unchanged');self.assertIsNone(select_due(self.a,self.config,self.now))

if __name__=='__main__':unittest.main()
