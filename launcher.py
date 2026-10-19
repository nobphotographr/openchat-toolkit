"""Reopen the authenticated local app, or start its dedicated server."""
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler
import webbrowser
import os
from settings import APP_ROOT, DATA_ROOT, ensure_home

def existing():
    try:
        value=json.loads((DATA_ROOT/'state/web-session.json').read_text(encoding='utf8'))
        if type(value['port']) is not int or not 1024<=value['port']<=65535:return None
        origin='http://127.0.0.1:'+str(value['port'])
        req=Request(origin+'/api/session',data=json.dumps({'token':value['token']}).encode(),headers={'Origin':origin,'Content-Type':'application/json'})
        with build_opener(ProxyHandler({})).open(req,timeout=2) as r:
            if r.status==200:return origin+'/#'+value['token']
    except (OSError,ValueError,KeyError,TypeError):pass

def main():
    ensure_home();url=existing()
    if not url:
        subprocess.Popen([sys.executable,str(APP_ROOT/'web_ui.py'),'--serve-only'],cwd=APP_ROOT,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(40):
            time.sleep(.25);url=existing()
            if url:break
    if not url:raise RuntimeError('OpenChat Toolkitを起動できませんでした。初期設定を確認してください。')
    edge=next((Path(os.environ.get(k,''))/'Microsoft/Edge/Application/msedge.exe' for k in ('PROGRAMFILES(X86)','PROGRAMFILES','LOCALAPPDATA') if (Path(os.environ.get(k,''))/'Microsoft/Edge/Application/msedge.exe').is_file()),None)
    if edge:subprocess.Popen([str(edge),'--app='+url,'--user-data-dir='+str(DATA_ROOT/'state/browser-profile'),'--no-first-run','--window-size=1400,940'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    else:webbrowser.open(url)

if __name__=='__main__':
    try:main()
    except Exception:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,'起動できませんでした。「初期設定.cmd」を実行してから再度お試しください。','OpenChat Toolkit',16)
        raise SystemExit(1)
