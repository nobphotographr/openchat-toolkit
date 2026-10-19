"""Explicit user setup, local import and bounded collection actions."""
import base64
from contextlib import closing
import os
from pathlib import Path
import subprocess
import sys
from collector import Archive, configured_rooms
from settings import APP_ROOT, DATA_ROOT, load_config, save_config, revision
from runtime_lock import WorkerLock, Refused
ACTIONS={'settings','save_settings','diagnostics','collect','import_mobile'}

def diagnostics():
    from session_probe import probe
    session=probe()
    line=Path(os.environ.get('LOCALAPPDATA',''))/'LINE/bin/current/LINE.exe'
    return dict(windows=os.name=='nt', line_installed=line.is_file(),
                helper_built=(APP_ROOT/'native/LineHistory.exe').is_file(),
                session_ready=session['ready'], session_reason=session['reason'],
                python_version=sys.version.split()[0],
                note='自動取得は日本語版LINE・画面倍率150%を検証対象としています。ロック中やスリープ中は取得できません。診断ではログイン状態や対象ルームへの参加までは確認しません。')

def handle(self,action,data):
    if action=='settings':
        config=load_config();return dict(config=config,revision=revision(config))
    if action=='save_settings':
        try:return dict(revision=save_config(data['config'],data['revision']))
        except Refused as e:raise ValueError(str(e)) from None
    if action=='diagnostics': return diagnostics()
    if action=='collect':
        if data.get('confirmed') is not True:raise ValueError('LINEを操作する確認が必要です。')
        room=data.get('room')
        if room not in configured_rooms():raise ValueError('登録済みのルームを選択してください。')
        if next(r for r in load_config()['rooms'] if r['name']==room)['collection_method']=='manual':
            raise ValueError('このルームは履歴ファイルを取り込んでください。')
        # Child acquires the same cross-process lock before UI navigation.
        subprocess.Popen([sys.executable,str(APP_ROOT/'worker.py'),'--room',room],
            cwd=APP_ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return dict(status='requested')
    if action=='import_mobile':
        room=data.get('room')
        if room not in configured_rooms():raise ValueError('登録済みのルームを選択してください。')
        encoded=data.get('base64')
        if not isinstance(encoded,str) or len(encoded)>7*1024*1024:raise ValueError('履歴は5MB以内で指定してください。')
        try:raw=base64.b64decode(encoded,validate=True)
        except ValueError:raise ValueError('ファイル形式が不正です。') from None
        if not 0<len(raw)<=5*1024*1024:raise ValueError('履歴は5MB以内で指定してください。')
        try:
            with WorkerLock(self.state/'collector-worker'), closing(Archive(self.state/'history.sqlite3')) as a:
                return a.ingest(room,raw,expected_room=room,source='ios_manual')
        except Refused as e:raise ValueError(str(e)) from None
