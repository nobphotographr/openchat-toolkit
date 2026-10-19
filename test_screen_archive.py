import copy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from collector import Archive,ROOT,InvalidExport
import screen_archive as screen
import archive_features as features


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'state'); self.path=Path(self.tmp.name)/'test.sqlite3'
        self.archive=Archive(self.path)
        self.rooms=patch('screen_archive.configured_rooms',return_value=['room']);self.rooms.start()
        self.time=datetime.now(timezone.utc)-timedelta(seconds=50)
    def tearDown(self): self.rooms.stop();self.archive.close();self.tmp.cleanup()
    def payload(self,bodies):
        self.time+=timedelta(seconds=1)
        return {'schema_version':1,'status':'screen_read','room':'room','observed_at':self.time.isoformat(),'bottom_confirmed':True,'send_invocations':0,'history_download_invocations':0,'pages':[{'messages':[{'body':b,'sha256':screen.digest(b)} for b in bodies]}],'skip_reasons':{},'skipped_rows':0}
    def ingest(self,bodies): return screen.ingest(self.archive,'room',self.payload(bodies))
    def test_baseline_reread_restart(self):
        self.assertEqual(self.ingest(['new','middle','old'])['new_records'],3)
        self.assertEqual(self.ingest(['new','middle','old'])['new_records'],0)
        self.archive.close();self.archive=Archive(self.path)
        self.assertEqual(self.ingest(['brandnew','new','middle','old'])['new_records'],1)
        self.assertEqual(features.search(self.archive.db)[0],4)
    def test_identical_new_message_with_context_is_distinct(self):
        self.ingest(['a','b','c'])
        self.assertEqual(self.ingest(['a','a','b','c'])['new_records'],1)
    def test_ambiguous_anchor_keeps_checkpoint_and_raw(self):
        self.ingest(['a','b','c']);before=self.archive.db.execute('SELECT hashes FROM screen_checkpoints').fetchone()
        r=self.ingest(['new','a','b','c','a','b','c']); self.assertEqual(r['status'],'screen_gap')
        self.assertEqual(self.archive.db.execute('SELECT hashes FROM screen_checkpoints').fetchone(),before)
        self.assertEqual(self.archive.db.execute('SELECT COUNT(*) FROM screen_scans').fetchone()[0],2)
    def test_missing_anchor_no_silent_advance(self):
        self.ingest(['a','b','c']);r=self.ingest(['x','y','z']);self.assertEqual(r['new_records'],0);self.assertEqual(r['status'],'screen_gap')
    def test_timestamp_distinction_and_keyword(self):
        features.add_rule(self.archive.db,'企画');self.ingest(['企画のお知らせ'])
        row=features.search(self.archive.db,mode='unread')[1][0]
        self.assertEqual(row['timestamp_kind'],'observed')
        self.assertIsNone(self.archive.status()[0]['latest_message'])
        self.assertIsNotNone(self.archive.status()[0]['latest_screen_observation'])
    def test_room_hash_and_action_contract(self):
        original=self.payload(['text'])
        for field,value in [('room','wrong'),('send_invocations',1),('history_download_invocations',1),('bottom_confirmed',False),('schema_version',True),('observed_at','2026-01-01')]:
            p=copy.deepcopy(original);p[field]=value
            with self.assertRaises(InvalidExport): screen.ingest(self.archive,'room',p)
        p=copy.deepcopy(original);p['pages'][0]['messages'][0]['body']='altered'
        with self.assertRaises(InvalidExport): screen.ingest(self.archive,'room',p)
        self.assertEqual(features.search(self.archive.db)[0],0)
    def test_page_overlap_runtime_id_and_mutation(self):
        p=self.payload(['a','b']);p['pages'][0]['messages'][0]['item_id']='1';p['pages'][0]['messages'][1]['item_id']='2'
        b=p['pages'][0]['messages'][1];p['pages'].append({'messages':[b,{'body':'c','sha256':screen.digest('c'),'item_id':'3'}]})
        self.assertEqual(screen.ingest(self.archive,'room',p)['new_records'],3)
        p=copy.deepcopy(p);p['pages'][1]['messages'][0]={'item_id':'2','body':'changed','sha256':screen.digest('changed')}
        with self.assertRaises(InvalidExport): screen.ingest(self.archive,'room',p)
    def test_copy_failure_does_not_advance(self):
        self.ingest(['a','b','c']);p=self.payload(['new','a','b','c']);p['skip_reasons']={'copy_not_observed':1}
        r=screen.ingest(self.archive,'room',p);self.assertEqual(r['status'],'screen_partial');self.assertEqual(r['new_records'],0)
        self.assertEqual(self.ingest(['new','a','b','c'])['new_records'],1)
    def test_old_response_is_not_processed(self):
        old=self.payload(['a','b','c']);self.ingest(['a','b','c'])
        self.assertEqual(screen.ingest(self.archive,'room',old)['status'],'screen_old_observation')
    def test_initial_mobile_overlap_not_imported_again(self):
        with self.archive.db:
            self.archive.db.execute('INSERT INTO records VALUES (?,?,1,?,?,?)',('room','mobile','2026-10-11T12:00:00+09:00','author\told body',self.time.isoformat()))
        r=self.ingest(['new body','old body']);self.assertEqual(r['new_records'],1);self.assertEqual(r['matched_mobile_history'],1)

    def test_older_unreadable_row_does_not_block_new_boundary(self):
        p=self.payload(['a','b','c'])
        for i,m in enumerate(p['pages'][0]['messages']):m['item_id']=str(i)
        p['pages'][0]['diagnostics']=[{'id':str(i),'reason':'copied'} for i in range(3)]+[{'id':'3','reason':'copy_visual_label_not_unique_0'}]
        p['skip_reasons']={'copy_visual_label_not_unique_0':1}
        r=screen.ingest(self.archive,'room',p)
        self.assertEqual(r['status'],'screen_baseline_limited');self.assertEqual(r['new_records'],3)
        self.assertEqual(self.ingest(['fresh','a','b','c'])['new_records'],1)

    def test_unreadable_new_row_preserves_boundary(self):
        self.ingest(['a','b','c']);p=self.payload(['fresh','a','b','c'])
        for i,m in enumerate(p['pages'][0]['messages']):m['item_id']=str(i)
        p['pages'][0]['diagnostics']=[{'id':'0','reason':'copied'},{'id':'missing','reason':'copy_not_observed'}]+[{'id':str(i),'reason':'copied'} for i in range(1,4)]
        p['skip_reasons']={'copy_not_observed':1}
        r=screen.ingest(self.archive,'room',p);self.assertEqual(r['status'],'screen_partial');self.assertEqual(r['new_records'],0)
    def test_one_absent_saved_text_keeps_archive_and_repairs_boundary(self):
        self.ingest(['a','b','c','d','e'])
        result=self.ingest(['fresh','a','c','d','e'])
        self.assertEqual(result['new_records'],1)
        self.assertEqual(result['boundary_alignment'],'one_saved_text_absent')
        self.assertEqual(self.archive.db.execute('SELECT COUNT(*) FROM records').fetchone()[0],6)
        self.assertEqual(self.ingest(['fresh','a','c','d','e'])['new_records'],0)
    def test_absent_text_without_new_posts_updates_boundary_without_duplicates(self):
        self.ingest(['a','b','c','d','e'])
        self.assertEqual(self.ingest(['a','c','d','e'])['new_records'],0)
        self.assertEqual(self.ingest(['a','c','d','e'])['boundary_alignment'],'exact')
        self.assertEqual(self.archive.db.execute('SELECT COUNT(*) FROM records').fetchone()[0],5)
    def test_absent_text_proof_rejects_short_ambiguous_or_unreadable_data(self):
        self.assertIsNone(screen.locate_anchor(['a','b','c','d'],['a','c','d']))
        self.assertIsNone(screen.locate_anchor(['a','b','c','d','e'],['a','d','e']))
        self.assertIsNone(screen.locate_anchor(['a','b','c','d','e'],['a','c','d','e','a','c','d','e']))
        self.assertIsNone(screen.locate_anchor(['a','b','c','d','e'],['a','unknown','c','d','e']))
        self.ingest(['a','b','c','d','e'])
        p=self.payload(['new','a','c','d','e']);p['skip_reasons']={'copy_not_observed':1}
        result=screen.ingest(self.archive,'room',p)
        self.assertEqual(result['new_records'],0);self.assertEqual(result['status'],'screen_partial')


if __name__=='__main__': unittest.main()
