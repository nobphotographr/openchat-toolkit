import base64
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
import zlib
from posting import Posts,ROOT,rooms,now,dispatch
import media
from media_delivery import dispatch_image
import recurrence


def png(width=80,height=50):
    def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    pixels=b''.join(b'\0'+bytes(v for x in range(width) for v in (x*255//width,y*255//height,(x+y)*127//max(width,height))) for y in range(height))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',width,height,8,2,0,0,0))+chunk(b'tEXt',b'comment\0private metadata')+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')


class Fake:
    def __init__(self,fail=None):self.fail=fail;self.sends=0
    def preflight(self,*a):
        if self.fail=='preflight':raise RuntimeError('preflight_failed')
    def prepare(self,*a):pass
    def verify_prepared(self,*a):pass
    def invoke(self,*a):
        self.sends+=1
        if self.fail=='invoke':raise RuntimeError('lost_reply')
    def confirm(self,*a):return self.fail!='confirm'
    def close(self):pass


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'state');self.state=Path(self.temp.name)
        self.store=Posts(self.state/'posting.sqlite3')
        self.ident=media.upload(self.state,base64.b64encode(png()).decode())['id']
        self.clock=now().replace(hour=10,minute=0,second=0,microsecond=0)+timedelta(days=1)
        self.store.pause(False)  # Synthetic test store only.
    def tearDown(self):self.store.close();self.temp.cleanup()
    def row(self,body=''):
        p=self.store.save(rooms()[0],body,image_id=self.ident)
        self.store.schedule(p['id'],p['version'],self.clock,current=self.clock-timedelta(minutes=1))
        return self.store.claim(self.clock)
    def test_metadata_removed_content_addressed_and_duplicate_upload(self):
        data=media.read(self.state,self.ident)
        self.assertNotIn(b'private metadata',data)
        self.assertEqual(hashlib.sha256(data).hexdigest(),self.ident)
        self.assertEqual(media.upload(self.state,base64.b64encode(png()).decode())['id'],self.ident)
    def test_bad_format_and_size_and_paths(self):
        for text in ['!',base64.b64encode(b'<svg/>').decode(),'A'*(14*1024*1024+1)]:
            with self.assertRaises(ValueError):media.upload(self.state,text)
        for ident in ['../x','X'*64,'a'*63,None]:
            with self.assertRaises(ValueError):media.path(self.state,ident)
    def test_image_only_draft_roundtrip_and_tamper_stops_claim(self):
        p=self.store.save(rooms()[0],'',image_id=self.ident)
        self.store.schedule(p['id'],p['version'],self.clock,current=self.clock-timedelta(minutes=1))
        media.path(self.state,self.ident).write_bytes(b'changed')
        self.assertIsNone(self.store.claim(self.clock))
        self.assertEqual(self.store.get(p['id'])['status'],'blocked')
    def test_changed_image_invalidates_approval(self):
        p=self.store.save(rooms()[0],'caption',image_id=self.ident)
        self.store.schedule(p['id'],p['version'],self.clock,current=self.clock-timedelta(minutes=1))
        with self.store.db:self.store.db.execute("UPDATE posts SET image_id='' WHERE id=?",(p['id'],))
        self.assertIsNone(self.store.claim(self.clock));self.assertEqual(self.store.get(p['id'])['reason'],'approval_mismatch')
    def test_image_only_and_no_repeat(self):
        row=self.row();text=Fake();image=Fake()
        self.assertEqual(dispatch_image(self.store,row,text,lambda _:image)['status'],'local_confirmed')
        self.assertEqual((text.sends,image.sends),(0,1))
        dispatch_image(self.store,row,text,lambda _:image)
        self.assertEqual(image.sends,1)
    def test_caption_then_image_and_both_parts_durable(self):
        row=self.row('caption');text=Fake();image=Fake()
        result=dispatch_image(self.store,row,text,lambda _:image)
        self.assertEqual(result['send_invocations'],2)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM post_parts WHERE status='local_confirmed'").fetchone()[0],2)
    def test_text_unknown_prevents_image(self):
        row=self.row('caption');text=Fake('confirm');image=Fake()
        result=dispatch_image(self.store,row,text,lambda _:image)
        self.assertEqual(result['status'],'needs_review');self.assertEqual(image.sends,0)
    def test_image_preflight_after_text_is_partial_never_draft_retry(self):
        row=self.row('caption');text=Fake();image=Fake('preflight')
        result=dispatch_image(self.store,row,text,lambda _:image)
        self.assertEqual(result['status'],'needs_review')
        saved=self.store.get(row['id'])
        with self.assertRaises(ValueError):self.store.change(saved['id'],saved['version'],'draft')
    def test_image_lost_reply_and_restart_never_resends(self):
        row=self.row();image=Fake('invoke')
        self.assertEqual(dispatch_image(self.store,row,Fake(),lambda _:image)['status'],'needs_review')
        self.store.close();self.store=Posts(self.state/'posting.sqlite3');self.store.recover()
        self.assertIsNone(self.store.claim(self.clock));self.assertEqual(image.sends,1)
    def test_recurring_image_is_frozen(self):
        p=self.store.save(rooms()[0],'',image_id=self.ident)
        rule=recurrence.create(self.store,p['id'],p['version'],dict(weekdays=list(range(7)),time='10:00',start=self.clock.date().isoformat(),end=''),self.clock-timedelta(minutes=1))
        self.assertEqual(rule['image_id'],self.ident)
        self.assertTrue(all(r['image_id']==self.ident for r in self.store.list()))
        count=len(self.store.list());recurrence.materialize(self.store,self.clock-timedelta(minutes=1));self.assertEqual(len(self.store.list()),count)


if __name__=='__main__':unittest.main()
