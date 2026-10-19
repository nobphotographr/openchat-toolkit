"""Exact room/file binding; a lost send response always requires manual review."""
import base64
import json
import subprocess
import time
from posting import ROOT,rooms
from posting_adapter import Session,native,new_identical_bubble
from runtime_lock import Refused
from session_probe import probe
import media


class ImageAdapter:
    def __init__(self,state,allow_send=True):
        self.state=state;self.allow_send=allow_send;self.room=None;self.session=None
        self.invoked=False;self.prepared=False;self.instance=None;self.baseline=None
        self.confirmation_checks=[]

    def bound(self,reply):
        if reply.get('window_instance')!=self.instance:raise Refused('image_window_changed')
        if reply.get('send_invocations')!=(1 if self.invoked else 0):raise Refused('image_invocation_mismatch')
        return reply

    def preflight(self,room,ident):
        if self.room is not None or room not in rooms():raise Refused('image_room_invalid')
        if not probe().get('ready'):raise Refused('session_unavailable')
        media.read(self.state,ident);self.ident=ident;self.path=media.path(self.state,ident);self.room=room
        ready=native(room,'--open')
        if ready.get('foreground') is not True:raise Refused('foreground_unconfirmed')
        self.baseline=native(room,'--image-baseline');self.baseline.pop('row_png',None)
        self.instance=self.baseline.get('window_instance')
        if not self.instance:raise Refused('image_window_unconfirmed')
        self.session=Session(room,self.allow_send,media=True)
        self.bound(self.session.request('preflight',path=str(self.path),sha256=ident))

    def prepare(self):
        self.bound(self.session.request('prepare'));self.prepared=True

    def verify_prepared(self):
        media.read(self.state,self.ident)
        self.bound(self.session.request('verify'))

    def invoke(self):
        if not self.allow_send or self.invoked or not self.prepared:raise Refused('image_invoke_not_allowed')
        self.invoked=True
        self.bound(self.session.request('invoke'))

    def confirm(self):
        if not self.invoked:raise Refused('image_not_invoked')
        self.session.close();self.session=None
        for delay in (1,2,3):
            time.sleep(delay)
            check={}
            try:
                after=native(self.room,'--image-baseline')
                check.update(same_window=after.get('window_instance')==self.instance,new_row=new_identical_bubble(self.baseline,after))
                if not check['same_window'] or not check['new_row']:continue
                result=subprocess.run([str(ROOT/'posting-native/ImageMatch.exe'),str(self.path)],input=after['row_png']+'\n',text=True,encoding='utf-8',capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                match=json.loads(result.stdout)
                check.update(match=match)
                if result.returncode==0 and match.get('matched') is True:return True
            except Exception as error:check['error']=str(error) if isinstance(error,Refused) else type(error).__name__
            finally:self.confirmation_checks.append(check)
        return False

    def clear_prepared(self):
        if self.invoked:raise Refused('image_already_invoked')
        self.bound(self.session.request('clear'));self.prepared=False

    def close(self):
        if self.session:
            if self.prepared and not self.invoked:
                try:self.clear_prepared()
                except Exception:pass
            self.session.close();self.session=None
        if self.room:
            try:native(self.room,'--close')
            except Exception:pass
