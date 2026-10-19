"""Standalone adapter for explicitly approved local posts."""
import json
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from posting import ROOT, now, validate

from runtime_lock import Refused
from native_transport import NativeSession, WindowsLineAdapter
from session_probe import probe

EXE=ROOT/'posting-native/PostLabLine.exe'


def new_identical_bubble(before,after):
    """Require both the old bubble and a genuinely new row, in the same window."""
    old=before.get('last_message_id');new=after.get('last_message_id')
    old_rows=before.get('visible_message_ids');new_rows=after.get('visible_message_ids')
    return (isinstance(old,str) and isinstance(new,str) and old!=new
            and isinstance(old_rows,list) and isinstance(new_rows,list)
            and old in old_rows and old in new_rows and new not in old_rows and new in new_rows)


def native(room,mode):
    result=subprocess.run([str(EXE),mode,room],capture_output=True,timeout=25,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    value=json.loads(result.stdout.decode('utf-8-sig'))
    if result.returncode or value.get('room')!=room or value.get('send_invocations')!=0:
        reason=value.get('message','')
        raise Refused(reason if isinstance(reason,str) and re.fullmatch('[a-z_0-9]{1,100}',reason) else 'native_navigation_or_read_failed')
    return value


class Session(NativeSession):
    def __init__(self,room,allow_send,media=False):
        self.timeout=12; self.room=room
        mode=('--media-send-session' if allow_send else '--media-draft-session') if media else ('--send-session' if allow_send else '--draft-session')
        self.process=subprocess.Popen([str(EXE),mode,room],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',bufsize=1,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.lines=queue.Queue(); threading.Thread(target=self._read,daemon=True).start()
        try:
            ready=self.receive()
            if ready.get('status')!='session_ready' or ready.get('room')!=room or ready.get('send_enabled') is not allow_send:
                raise Refused('session_not_ready')
        except BaseException:
            self.close(); raise


class Adapter(WindowsLineAdapter):
    def __init__(self,allow_send=True):
        super().__init__(now().date().isoformat(),allow_test_send=allow_send)
        self.room=None; self.before_text=None;self.baseline=None

    def check(self,destination,text):
        validate(destination,text)
        if self.room is not None and self.room!=destination: raise Refused('room_changed')
        if self.message is not None and self.message!=text: raise Refused('message_changed')

    def preflight(self,destination,text):
        self.check(destination,text)
        if not probe().get('ready'): raise Refused('session_unavailable')
        self.room=destination
        ready=native(destination,'--open')
        if ready.get('foreground') is not True: raise Refused('foreground_unconfirmed')
        before=native(destination,'--message-baseline')
        if before.get('status')!='message_baseline' or before.get('input_empty_before_copy') is not True:
            raise Refused('baseline_unconfirmed')
        self.before_text=before.get('last_message'); self.instance=before.get('window_instance');self.baseline=before
        if not self.instance: raise Refused('window_instance_missing')
        if self.before_text==text and (not before.get('last_message_id') or before.get('last_message_id') not in before.get('visible_message_ids',[])):
            raise Refused('same_text_identity_unavailable_review_required')
        self.session=Session(destination,self.can_send)
        self.bound_reply(self.session.request('preflight',message=text,business_date=self.business_date),empty=True)
        self.message=text

    def confirm(self,destination,text):
        self.check(destination,text)
        if self.send_invocations!=1: raise Refused('send_not_invoked')
        self.session.close(); self.session=None
        for delay in (1,2,3):
            time.sleep(delay)
            try:
                after=native(destination,'--read-last-message')
                if (after.get('status')=='message_read' and after.get('input_empty_before_copy') is True
                    and after.get('window_instance')==self.instance and after.get('outgoing') is True
                    and after.get('last_message')==text
                    and (self.before_text!=text or new_identical_bubble(self.baseline,after))): return True
            except Exception: pass
        return False

    def prepare(self,destination,text):
        super().prepare(destination,text)
        self.attempt['destination']=destination

    def close(self):
        super().close()
        if self.room:
            # Native refuses to close changed/nonempty drafts or dialogs.
            try: native(self.room,'--close')
            except Exception: pass
