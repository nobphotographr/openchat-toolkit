from pathlib import Path
import sqlite3
import tempfile
import unittest
from posting import ROOT,Posts,rooms
import templates


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'state');self.path=Path(self.temp.name)/'posting.sqlite3';self.store=Posts(self.path)
        self.store.pause(False)  # Synthetic test store only.
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_create_edit_rename_group_and_conflict(self):
        row=templates.save(self.store,'案内','本文','企画')
        edited=templates.save(self.store,'次回の案内','変更後','日常',original=row['name'],version=row['version'])
        self.assertEqual(edited['group_name'],'日常');self.assertEqual(len(templates.listing(self.store)),1)
        with self.assertRaises(ValueError):templates.save(self.store,'上書き','競合',original=edited['name'],version=row['version'])
        self.assertEqual(templates.get(self.store,edited['name'])['body'],'変更後')
    def test_duplicate_unique_and_independent(self):
        row=templates.save(self.store,'案内','本文')
        a=templates.duplicate(self.store,row['name'],row['version']);b=templates.duplicate(self.store,row['name'],row['version'])
        self.assertNotEqual(a['name'],b['name'])
        templates.save(self.store,a['name'],'別の本文',original=a['name'],version=a['version'])
        self.assertEqual(templates.get(self.store,row['name'])['body'],'本文')
    def test_archive_restore_and_restart(self):
        row=templates.save(self.store,'案内','本文');row=templates.archive(self.store,row['name'],row['version'],True)
        self.assertEqual(templates.listing(self.store,False),[])
        self.store.close();self.store=Posts(self.path)
        templates.archive(self.store,row['name'],row['version'],False)
        self.assertEqual(len(templates.listing(self.store,False)),1)
    def test_edit_does_not_change_saved_post(self):
        row=templates.save(self.store,'案内','本文');post=self.store.save(rooms()[0],row['body'])
        templates.save(self.store,row['name'],'別の本文',original=row['name'],version=row['version'])
        self.assertEqual(self.store.get(post['id'])['body'],'本文')
    def test_duplicate_names_and_controls_rejected(self):
        templates.save(self.store,'案内','本文')
        for name in ('案内','','X'*81,'a\nb'):
            with self.assertRaises(ValueError):templates.save(self.store,name,'本文')
        with self.assertRaises(ValueError):templates.save(self.store,'別の案内','本文','X'*41)
    def test_stale_archive_or_duplicate_and_conflicting_rename(self):
        row=templates.save(self.store,'案内','本文');templates.save(self.store,'既存','保持')
        with self.assertRaises(ValueError):templates.save(self.store,'既存','上書き',original=row['name'],version=row['version'])
        templates.archive(self.store,row['name'],row['version'],True)
        with self.assertRaises(ValueError):templates.archive(self.store,row['name'],row['version'],False)
        with self.assertRaises(ValueError):templates.duplicate(self.store,row['name'],row['version'])
    def test_legacy_table_migrates_without_changing_text(self):
        self.store.close();self.path.unlink()
        db=sqlite3.connect(self.path);db.execute('CREATE TABLE post_templates(name TEXT PRIMARY KEY,body TEXT NOT NULL)');db.execute('INSERT INTO post_templates VALUES(?,?)',('既存の定型文','以前の本文'));db.commit();db.close()
        self.store=Posts(self.path)
        self.assertEqual(templates.get(self.store,'既存の定型文')['body'],'以前の本文')
        self.store.template('既存の定型文','旧UIから編集')
        self.assertEqual(templates.get(self.store,'既存の定型文')['version'],2)


if __name__=='__main__':unittest.main()
