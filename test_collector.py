import sqlite3
import tempfile
import unittest
from pathlib import Path
from collector import Archive, InvalidExport, parse_export, ROOT

ROOM = 'サンプルルーム'
OTHER = '別のサンプルルーム'
def sample(body='12:00 名前 本文\n次の行\n'):
    return ('2026.10.12 月曜日\n' + body).encode('utf-8')


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'state')
        self.path = Path(self.tmp.name)/'db.sqlite3'
        self.archive = Archive(self.path)
    def tearDown(self):
        self.archive.close()
        self.tmp.cleanup()
    def add(self, data, room=ROOM):
        return self.archive.ingest(room, data, expected_room=room)
    def test_synthetic_windows_export_repeated(self):
        raw = sample('12:00 Sample synthetic history\n')
        first = self.add(raw)
        self.assertGreater(first['new_records'], 0)
        self.assertEqual(self.add(raw)['new_records'], 0)
        self.assertEqual(self.archive.db.execute('SELECT raw FROM snapshots').fetchone()[0], raw)
    def test_restart(self):
        self.add(sample()); self.archive.close(); self.archive = Archive(self.path)
        self.assertEqual(self.add(sample())['new_records'], 0)
    def test_multiline_and_name_spaces_preserved(self):
        parsed = parse_export(sample('12:00 Name With Spaces text\nURL https://example.org\n'))
        self.assertEqual(parsed[0][1], 'Name With Spaces text\nURL https://example.org')
    def test_repeated_identical_same_minute(self):
        data = sample('12:00 名前 同じ\n12:00 名前 同じ\n')
        self.assertEqual(self.add(data)['new_records'], 2)
        self.assertEqual(self.add(data)['new_records'], 0)
        self.assertEqual(self.add(sample('12:00 名前 同じ\n'*3))['new_records'], 1)
    def test_growing_snapshot(self):
        self.add(sample()); self.assertEqual(self.add(sample() + '12:01 名前 追加\n'.encode())['new_records'], 1)
    def test_shrinking_snapshot_does_not_delete(self):
        self.add(sample() + '12:01 名前 追加\n'.encode()); self.add(sample())
        self.assertEqual(self.archive.status()[0]['records'], 2)
    def test_rooms_do_not_mix(self):
        self.add(sample()); self.add(sample(), OTHER)
        self.assertEqual(len(self.archive.status()), 2)
    def test_wrong_room_rejected(self):
        with self.assertRaises(InvalidExport): self.archive.ingest(ROOM, sample(), expected_room=OTHER)
        self.assertEqual(self.archive.status(), [])
    def test_unknown_room_rejected(self):
        with self.assertRaises(InvalidExport): self.add(sample(), 'unknown')
    def test_crlf_and_bom_preserve_raw_but_dedup_records(self):
        self.add(sample())
        data = b'\xef\xbb\xbf'+sample().replace(b'\n',b'\r\n')
        self.assertEqual(self.add(data)['new_records'], 0)
        self.assertEqual(self.archive.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 2)
    def test_failed_transaction_rolls_back(self):
        self.archive.db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON snapshot_records BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.add(sample())
        self.assertEqual(self.archive.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 0)
        self.assertEqual(self.archive.status(), [])
    def test_invalid_data(self):
        for data in (b'', b'\xff', b'not export', b'12:00 text', sample('24:00 Name bad'), b'\x00',
                     '2026.02.30 月曜日\n12:00 名前 本文'.encode(), '2026.10.12 月曜日'.encode()):
            with self.subTest(data=data):
                with self.assertRaises(InvalidExport): self.add(data)
        self.assertEqual(self.archive.status(), [])
    def test_body_timestamp_ambiguity_raw_snapshot_survives(self):
        # The plain text format has no message IDs or escaping. Keep exact source
        # even where a body line could be interpreted as a new message boundary.
        data = sample('12:00 名前 時刻の例\n13:00 本文内の時刻\n')
        self.add(data)
        self.assertEqual(self.archive.db.execute('SELECT raw FROM snapshots').fetchone()[0], data)


if __name__ == '__main__': unittest.main()
