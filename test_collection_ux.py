import json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from collector import Archive,ROOT
import screen_resume,collection_retry,collection_view
from collection_schedule import select_due
import archive_features as features


class CollectionUXTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'state')
        self.a=Archive(Path(self.tmp.name)/'history.sqlite3');self.db=self.a.db
        self.now=datetime.now(timezone.utc)
        self.config=dict(enabled=True,rooms=[dict(name=n,enabled=True) for n in ('ok','review','missing')],daily_schedule=dict(timezone='Asia/Tokyo',time='03:00',window_minutes=60))
        screen_resume.initialize(self.db)
        with self.db:
            self.db.execute('INSERT INTO screen_checkpoints VALUES (?,?,?,?)',('review','["a","b","c"]',self.now.isoformat(),3))
            checkpoint=screen_resume.checkpoint(self.db,'review')
            self.db.execute('INSERT INTO screen_resume_jobs VALUES (?,?,?,?,?,?,?,?,?,?)',('review','job',json.dumps(checkpoint),'[]',self.now.isoformat(),30,'review',self.now.isoformat(),3,0))
            self.db.execute('INSERT INTO screen_resume_chunks(job_id,room,observed_at,payload,decision) VALUES (?,?,?,?,?)',('job','review',self.now.isoformat(),json.dumps({'skip_reasons':{'copy_visual_label_not_unique_0':2},'private_text':'DO NOT EXPOSE'}),'boundary_not_reached'))
            for name,status in [('ok','imported'),('review','screen_resume_review')]:
                self.db.execute('INSERT INTO attempts(room,started_at,status) VALUES (?,?,?)',(name,self.now.isoformat(),status))
    def tearDown(self):self.a.close();self.tmp.cleanup()
    def test_health_partition_and_actionable_cause(self):
        data=collection_view.build(self.a,self.config,self.now)
        self.assertEqual(data['summary'],dict(complete=1,pending=0,review=1,missing=1,disabled=0,total=3))
        row=data['rooms'][0];self.assertEqual(row['room'],'review');self.assertTrue(row['can_retry'])
        self.assertIn('コピー操作',row['cause']);self.assertNotIn('DO NOT EXPOSE',json.dumps(data))
    def test_retry_is_durable_one_shot_and_preserves_buffer_and_checkpoint(self):
        with self.db:self.db.execute('UPDATE screen_resume_jobs SET messages=?',(json.dumps(['途中保存した本文']),))
        before=self.db.execute('SELECT checkpoint,messages,next_step FROM screen_resume_jobs').fetchone()
        collection_retry.request(self.db,'review',self.config,self.now)
        collection_retry.request(self.db,'review',self.config,self.now)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM collection_retry_requests').fetchone()[0],1)
        path=self.a.path if hasattr(self.a,'path') else Path(self.tmp.name)/'history.sqlite3'
        self.a.close();self.a=Archive(path);self.db=self.a.db
        self.assertEqual(select_due(self.a,self.config,self.now),'review')
        self.assertTrue(collection_retry.consume(self.db,'review',self.now))
        self.assertFalse(collection_retry.consume(self.db,'review',self.now))
        self.assertEqual(before,self.db.execute('SELECT checkpoint,messages,next_step FROM screen_resume_jobs').fetchone())
        self.assertEqual(self.db.execute('SELECT phase,stalls FROM screen_resume_jobs').fetchone(),('pending',0))
    def test_unknown_disabled_and_changed_checkpoint_are_rejected(self):
        for room in ('other','ok'):
            with self.assertRaises(ValueError):collection_retry.request(self.db,room,self.config,self.now)
        self.config['enabled']=False
        with self.assertRaises(ValueError):collection_retry.request(self.db,'review',self.config,self.now)
        self.config['enabled']=True
        with self.db:self.db.execute('UPDATE screen_checkpoints SET sequence=4')
        with self.assertRaises(ValueError):collection_retry.request(self.db,'review',self.config,self.now)
    def test_expired_request_cannot_reset_a_review_job(self):
        collection_retry.request(self.db,'review',self.config,self.now)
        later=self.now+timedelta(hours=25)
        self.assertIsNone(collection_retry.due(self.db,self.config,later))
        self.assertFalse(collection_retry.consume(self.db,'review',later))
        self.assertEqual(self.db.execute('SELECT phase FROM screen_resume_jobs').fetchone()[0],'review')
    def test_guard_changed_after_request(self):
        collection_retry.request(self.db,'review',self.config,self.now)
        with self.db:self.db.execute('UPDATE screen_checkpoints SET sequence=4')
        self.assertIsNone(collection_retry.due(self.db,self.config,self.now))
        self.assertFalse(collection_retry.consume(self.db,'review',self.now))
    def test_expired_unconsumed_request_can_be_requested_again(self):
        collection_retry.request(self.db,'review',self.config,self.now)
        later=self.now+timedelta(hours=25)
        self.assertIsNone(collection_retry.due(self.db,self.config,later))
        collection_retry.request(self.db,'review',self.config,later)
        self.assertEqual(collection_retry.due(self.db,self.config,later),'review')
        self.assertTrue(collection_retry.consume(self.db,'review',later))
    def test_review_does_not_disappear_the_next_day(self):
        data=collection_view.build(self.a,self.config,self.now+timedelta(days=1))
        self.assertEqual(data['summary']['review'],1);self.assertEqual(data['summary']['complete'],0)
    def test_bookmark_status_is_returned_by_search(self):
        features.initialize(self.db)
        with self.db:self.db.execute('INSERT INTO records VALUES (?,?,?,?,?,?)',('ok','fingerprint',1,self.now.isoformat(),'本文',self.now.isoformat()))
        record=features.search(self.db)[1][0];self.assertFalse(record['saved'])
        features.toggle_bookmark(self.db,record)
        self.assertTrue(features.search(self.db)[1][0]['saved'])
        features.toggle_bookmark(self.db,record)
        self.assertFalse(features.search(self.db)[1][0]['saved'])

if __name__=='__main__':unittest.main()
