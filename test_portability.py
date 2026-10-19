"""Boundary tests for the distributable application; synthetic data only."""
from copy import deepcopy
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import settings
from posting import Posts
from runtime_lock import WorkerLock, Refused
import setup_api
from test_mobile import mobile

class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.home=patch.object(settings,'DATA_ROOT',self.root);self.home.start()
        settings.ensure_home()
    def tearDown(self):
        self.home.stop();self.tmp.cleanup()
    def config(self):
        value=deepcopy(settings.DEFAULT)
        value['rooms']=[dict(name='合成ルーム',enabled=False,collection_method='export',pc_history_download=True)]
        return value
    def save(self):
        settings.save_config(self.config(),settings.revision(settings.load_config()))
    def test_empty_install_starts_disabled_and_paused(self):
        self.assertEqual(settings.load_config()['rooms'],[])
        self.assertFalse(settings.load_config()['enabled'])
        p=Posts(self.root/'state/posting.sqlite3')
        try:self.assertTrue(p.paused());self.assertEqual(p.list(),[])
        finally:p.close()
    def test_config_revision_and_native_room_allowlist_are_current(self):
        rev=settings.revision(settings.load_config());self.save()
        import posting
        self.assertEqual(posting.rooms(),['合成ルーム'])
        with self.assertRaises(ValueError):settings.save_config(self.config(),rev)
    def test_config_cannot_change_during_posting(self):
        with WorkerLock(self.root/'state/posting-worker'):
            with self.assertRaises(Refused):self.save()
        self.assertEqual(settings.load_config()['rooms'],[])
    def test_config_cannot_change_during_collection(self):
        with WorkerLock(self.root/'state/collector-worker'):
            with self.assertRaises(Refused):self.save()
    def test_unknown_rooms_and_invalid_methods_rejected(self):
        for change in ({'name':'LINE'},{'name':' a '},{'enabled':1},{'collection_method':'manual','enabled':True}):
            value=self.config();value['rooms'][0].update(change)
            with self.assertRaises(ValueError):settings.validate_config(value)
    def test_task_runner_is_paused_without_probing_desktop(self):
        import posting_worker
        with patch.object(posting_worker,'ROOT',self.root),patch.object(posting_worker,'Posts',side_effect=lambda:Posts(self.root/'state/posting.sqlite3')),patch.object(posting_worker,'probe') as probe:
            self.assertEqual(posting_worker.run()['status'],'paused');probe.assert_not_called()
    def test_manual_collection_requires_confirmation_and_registered_room(self):
        self.save();app=Mock(state=self.root/'state')
        with patch('setup_api.subprocess.Popen') as child:
            for data in [dict(room='合成ルーム'),dict(room='未登録',confirmed=True)]:
                with self.assertRaises(ValueError):setup_api.handle(app,'collect',data)
            child.assert_not_called()
    def test_smartphone_import_preserves_old_data_and_rejects_other_room(self):
        self.save();app=Mock(state=self.root/'state')
        data=dict(room='合成ルーム',base64=base64.b64encode(mobile(room='合成ルーム')).decode())
        self.assertEqual(setup_api.handle(app,'import_mobile',data)['new_records'],1)
        self.assertEqual(setup_api.handle(app,'import_mobile',data)['new_records'],0)
        data['base64']=base64.b64encode(mobile(room='別室')).decode()
        with self.assertRaises(ValueError):setup_api.handle(app,'import_mobile',data)
    def test_operations_only_read_local_posting_and_collection(self):
        from operations import overview
        value=overview(self.root/'missing')
        self.assertEqual({s['key'] for s in value['services']},{'posting','collector'})
        self.assertFalse((self.root/'missing').exists())
    def test_alternate_state_preview_cannot_change_live_config_or_collect(self):
        from web_ui import Application
        app=Application(self.root/'preview')
        with patch('setup_api.handle') as handler:
            for action in ('save_settings','collect'):
                with self.assertRaises(ValueError):app.call(action,{})
            handler.assert_not_called()

class TransportTests(unittest.TestCase):
    def adapter(self):
        from posting_adapter import Adapter
        a=Adapter();a.room='合成ルーム';a.message='合成本文';a.instance='w';a.session=Mock()
        return a
    def test_window_and_draft_binding_rejects_changed_receipts(self):
        a=self.adapter()
        for receipt in [dict(window_instance='other',draft_exact=True),dict(window_instance='w',draft_exact=False)]:
            with self.assertRaises(Refused):a.bound_reply(receipt,draft=True)
    def test_lost_send_response_consumes_single_attempt(self):
        a=self.adapter();a.session.request.side_effect=Refused('lost_reply')
        with patch('posting_adapter.validate'):
            with self.assertRaises(Refused):a.invoke(a.room,a.message)
            with self.assertRaises(Refused):a.invoke(a.room,a.message)
        self.assertEqual(a.session.request.call_count,1)
    def test_disabled_adapter_never_invokes(self):
        a=self.adapter();a.can_send=False
        with patch('posting_adapter.validate'):
            with self.assertRaises(Refused):a.invoke(a.room,a.message)
        a.session.request.assert_not_called()

if __name__=='__main__':unittest.main()
