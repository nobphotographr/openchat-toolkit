import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from collector import Archive, ROOT
import archive_features as f


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'state')
        self.path=Path(self.tmp.name)/'archive.sqlite3'
        self.archive=Archive(self.path); self.db=self.archive.db
        self.patch=patch('collector.configured_rooms',return_value=['room A','room B']); self.patch.start()

    def tearDown(self):
        self.patch.stop(); self.archive.close(); self.tmp.cleanup()

    def ingest(self,text,room='room A',day='2026.10.12'):
        data=(day+' 月曜日\n12:00 著者 '+text+'\n').encode()
        return self.archive.ingest(room,data,expected_room=room)

    def test_new_only_boundary_and_case_literal(self):
        self.ingest('OLD Ai%_'); f.add_rule(self.db,'AI%_')
        self.assertEqual(f.search(self.db,mode='unread')[0],0)
        self.ingest('new ai%_')
        self.ingest('new aiXX')
        self.assertEqual(f.search(self.db,mode='unread')[0],1)

    def test_reimport_and_restart_read_persistence(self):
        f.add_rule(self.db,'募集'); self.ingest('募集開始')
        self.ingest('募集開始'); f.refresh(self.db)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM keyword_hits').fetchone()[0],1)
        row=f.search(self.db,mode='unread')[1][0]; f.mark_read(self.db,row)
        self.archive.close(); self.archive=Archive(self.path); self.db=self.archive.db
        f.refresh(self.db)
        self.assertEqual(f.search(self.db,mode='unread')[0],0)
        self.assertEqual(f.search(self.db,mode='inbox')[0],1)

    def test_room_scope_historical_and_overlapping_rules(self):
        self.ingest('募集開始','room A'); self.ingest('募集開始','room B')
        f.add_rule(self.db,'募集','room A',True); f.add_rule(self.db,'開始','room A',True)
        self.assertEqual(f.search(self.db,mode='inbox')[0],1)
        row=f.search(self.db,mode='inbox')[1][0]; f.mark_read(self.db,row)
        self.assertEqual(self.db.execute('SELECT SUM(is_read) FROM keyword_hits').fetchone()[0],2)

    def test_failed_index_rolls_back_cursor_and_recovers_same_snapshot(self):
        f.add_rule(self.db,'募集')
        self.db.executescript("CREATE TRIGGER fail_hit BEFORE INSERT ON keyword_hits BEGIN SELECT RAISE(ABORT,'test failure'); END;")
        with self.assertRaises(sqlite3.IntegrityError): self.ingest('募集開始')
        self.assertEqual(self.db.execute('SELECT cursor FROM keyword_rules').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM records').fetchone()[0],1)
        self.db.execute('DROP TRIGGER fail_hit'); self.db.commit()
        result=self.ingest('募集開始')
        self.assertEqual(result['status'],'unchanged')
        self.assertEqual(f.search(self.db,mode='unread')[0],1)

    def test_pause_resume(self):
        f.add_rule(self.db,'募集')
        with self.db: self.db.execute('UPDATE keyword_rules SET enabled=0')
        self.ingest('募集開始'); self.assertEqual(f.search(self.db,mode='inbox')[0],0)
        with self.db: self.db.execute('UPDATE keyword_rules SET enabled=1')
        f.refresh(self.db); self.assertEqual(f.search(self.db,mode='inbox')[0],1)

    def test_search_filters_pagination_and_injection(self):
        self.ingest('募集100%_A'); self.ingest('募集 B','room B'); self.ingest('old',day='2026.10.11')
        self.assertEqual(f.search(self.db,query="' OR 1=1 --")[0],0)
        self.assertEqual(f.search(self.db,query='%_')[0],1)
        self.assertEqual(f.search(self.db,room='room A',start='2026-10-12',end='2026-10-12')[0],1)
        count,rows=f.search(self.db,limit=1,offset=1); self.assertEqual(count,3); self.assertEqual(len(rows),1)
        with self.assertRaises(ValueError): f.search(self.db,start='2026-10-31',end='2026-10-12')
        with self.assertRaises(ValueError): f.search(self.db,end='2026-99-01')

    def test_urls_are_explicit_safe_and_unique(self):
        self.assertEqual(f.links('リンク（https://example.com/path?q=1）。 https://example.com/path?q=1\njavascript:alert(1) file:///secret https://user:password@example.com'),['https://example.com/path?q=1'])
        self.assertEqual(f.links('https://[invalid https:// https://example.com/wiki/A_(B)'),['https://example.com/wiki/A_(B)'])
        self.ingest('https://example.com'); self.ingest('plain')
        self.assertEqual(f.search(self.db,mode='links')[0],1)

    def test_bookmarks_survive_restart(self):
        self.ingest('saved'); row=f.search(self.db)[1][0]
        self.assertTrue(f.toggle_bookmark(self.db,row)); self.archive.close()
        self.archive=Archive(self.path); self.db=self.archive.db
        self.assertEqual(f.search(self.db,mode='bookmarks')[0],1)
        self.assertFalse(f.toggle_bookmark(self.db,row)); self.assertEqual(f.search(self.db,mode='bookmarks')[0],0)

    def test_multiple_same_minute_occurrences_not_collapsed(self):
        f.add_rule(self.db,'募集')
        data='2026.10.12 月曜日\n12:00 著者 募集\n12:00 著者 募集\n'.encode()
        for _ in range(2): self.archive.ingest('room A',data,expected_room='room A')
        self.assertEqual(f.search(self.db,mode='inbox')[0],2)

    def test_invalid_or_duplicate_rule_preserves_data(self):
        with self.assertRaises(ValueError): f.add_rule(self.db,' ')
        f.add_rule(self.db,'Note')
        with self.assertRaises(sqlite3.IntegrityError): f.add_rule(self.db,'NOTE')
        self.ingest('note'); self.assertEqual(f.search(self.db,mode='inbox')[0],1)


if __name__=='__main__': unittest.main()
