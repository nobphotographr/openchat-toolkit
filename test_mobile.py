import csv
import io
import unicodedata
from collector import Archive, InvalidExport
from mobile_export import parse_mobile
from test_collector import ROOM, sample
import unittest
import tempfile
from pathlib import Path
from collector import ROOT

def mobile(room=ROOM,body='本文\n次の行',time='12:00',saved='2026/10/12 13:00'):
    s=io.StringIO(newline='')
    w=csv.writer(s,delimiter='\t',lineterminator='\n')
    w.writerows([['[LINE] '+room+'のトーク'],['保存日時：'+saved],[],['2026/10/12(月)'],[time,'名前',body]])
    return s.getvalue().encode('utf-8')

class MobileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'state');self.path=Path(self.tmp.name)/'db.sqlite3';self.archive=Archive(self.path)
    def tearDown(self):self.archive.close();self.tmp.cleanup()
    def add(self,data):return self.archive.ingest(ROOM,data,expected_room=ROOM,source='ios_manual')
    def test_quoted_multiline_and_tabs(self):
        body='本文\tタブ\n"引用"\n2026/10/11(日)\n12:30\t本文内の時刻'
        self.assertEqual(parse_mobile(mobile(body=body),ROOM)[0][1],'名前\t'+body)
    def test_header_is_verified_not_filename(self):
        with self.assertRaises(InvalidExport):self.add(mobile(room='別の部屋'))
        self.assertEqual(self.archive.status(),[])
    def test_unicode_normalized_room_name(self):
        room='サンプル🐰部屋'
        self.assertEqual(len(parse_mobile(mobile(room=unicodedata.normalize('NFD',room)),room)),1)
    def test_repeated_import_and_restart(self):
        raw=mobile();self.assertEqual(self.add(raw)['new_records'],1)
        self.archive.close();self.archive=Archive(self.path)
        self.assertEqual(self.add(raw)['new_records'],0)
        self.assertEqual(self.archive.db.execute('SELECT raw FROM snapshots').fetchone()[0],raw)
    def test_only_saved_at_changed_does_not_duplicate(self):
        self.add(mobile());self.assertEqual(self.add(mobile(saved='2026/10/12 14:00'))['new_records'],0)
    def test_mobile_not_implicitly_merged_with_windows(self):
        self.archive.ingest(ROOM,sample(),expected_room=ROOM)
        self.assertEqual(self.add(mobile())['new_records'],1)
        self.assertEqual(self.archive.status()[0]['records'],2)
    def test_invalid_time_and_truncated_quote(self):
        for data in [mobile(time='24:00'),mobile(saved='invalid'),mobile()[:-2],b'\xff']:
            with self.subTest(data=data):
                with self.assertRaises(InvalidExport):self.add(data)
        self.assertEqual(self.archive.status(),[])

if __name__=='__main__':unittest.main()
