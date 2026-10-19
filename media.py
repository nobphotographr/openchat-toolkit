"""Local image storage. Re-encode PNG/JPEG; never expose arbitrary file paths."""
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from posting import ROOT


def path(state, ident):
    if not isinstance(ident,str) or not re.fullmatch(r'[0-9a-f]{64}',ident):
        raise ValueError('画像IDが不正です。')
    folder=(Path(state)/'media').resolve()
    result=folder/(ident+'.png')
    if result.resolve().parent!=folder: raise ValueError('画像の保存先が不正です。')
    return result


def read(state, ident):
    target=path(state,ident)
    try:
        if target.stat().st_size>20*1024*1024:raise ValueError('保存画像が大きすぎます。')
        data=target.read_bytes()
    except OSError:raise ValueError('保存画像を読み取れません。') from None
    if hashlib.sha256(data).hexdigest()!=ident: raise ValueError('保存画像が変更されています。予約を作り直してください。')
    return data


def upload(state, encoded):
    if not isinstance(encoded,str) or len(encoded)>14*1024*1024: raise ValueError('画像は10MB以内にしてください。')
    try: raw=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error):raise ValueError('画像を読み取れません。') from None
    if not 0<len(raw)<=10*1024*1024:raise ValueError('画像は10MB以内にしてください。')
    try:
        process=subprocess.run([str(ROOT/'posting-native/ImageCodec.exe')],input=encoded+'\n',capture_output=True,text=True,encoding='utf-8',timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        result=json.loads(process.stdout)
        if process.returncode or result.get('ok') is not True:raise ValueError()
        data=base64.b64decode(result['png'],validate=True)
    except (ValueError,KeyError,OSError,subprocess.TimeoutExpired):
        raise ValueError('PNGまたはJPEG画像を選んでください（最大8,192px・2,400万画素）。') from None
    ident=hashlib.sha256(data).hexdigest();target=path(state,ident)
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=target.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
        os.replace(tmp,target)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return dict(id=ident,width=result['width'],height=result['height'],bytes=len(data))
