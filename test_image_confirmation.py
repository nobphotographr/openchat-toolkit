"""Image confirmation guards with synthetic protocol replies; no real LINE access."""
import base64
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch,Mock
from posting import ROOT
from media_adapter import ImageAdapter

class ConfirmationBindingTests(unittest.TestCase):
    def adapter(self):
        a=ImageAdapter(Path('.'));a.invoked=True;a.room='test';a.instance='window';a.session=Mock();a.path=Path('approved.png')
        a.baseline=dict(last_message_id='old',visible_message_ids=['old'])
        return a

    def test_same_bubble_or_different_window_never_compares(self):
        for after in [dict(window_instance='window',last_message_id='old',visible_message_ids=['old']),
                      dict(window_instance='other',last_message_id='new',visible_message_ids=['old','new'])]:
            a=self.adapter()
            with patch('media_adapter.native',return_value=after),patch('media_adapter.time.sleep'),patch('media_adapter.subprocess.run') as matcher:
                self.assertFalse(a.confirm());matcher.assert_not_called()

    def test_new_bubble_still_requires_image_match(self):
        after=dict(window_instance='window',last_message_id='new',visible_message_ids=['old','new'],row_png='private-pixels')
        for matched in (False,True):
            a=self.adapter()
            with patch('media_adapter.native',return_value=after),patch('media_adapter.time.sleep'),patch('media_adapter.subprocess.run',return_value=Mock(returncode=0,stdout=json.dumps(dict(matched=matched)))):
                self.assertEqual(a.confirm(),matched)
                self.assertNotIn('private-pixels',str(a.confirmation_checks))

if __name__=='__main__':unittest.main()
