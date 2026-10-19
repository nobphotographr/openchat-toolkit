"""Portable, user-owned configuration; no production installation dependency."""
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
import uuid

APP_ROOT = Path(__file__).resolve().parent
DATA_ROOT = APP_ROOT
DEFAULT = dict(schema_version=1, enabled=False, interval_seconds=86400,
               daily_schedule=dict(timezone='Asia/Tokyo', time='03:00', window_minutes=60), rooms=[])

def validate_config(data):
    if not isinstance(data, dict) or set(data) != set(DEFAULT):
        raise ValueError('設定の形式が不正です。')
    if type(data['schema_version']) is not int or data['schema_version'] != 1 or type(data['enabled']) is not bool:
        raise ValueError('設定バージョンまたは有効状態が不正です。')
    if type(data['interval_seconds']) is not int or data['interval_seconds'] != 86400:
        raise ValueError('巡回は1日1回です。')
    daily=data['daily_schedule']
    if not isinstance(daily,dict) or set(daily) != {'timezone','time','window_minutes'} or daily['timezone'] != 'Asia/Tokyo' or not isinstance(daily['time'],str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',daily['time']) or type(daily['window_minutes']) is not int or daily['window_minutes'] != 60:
        raise ValueError('取得時刻は日本時間の HH:MM で指定してください。')
    rooms=data['rooms']
    if not isinstance(rooms,list) or len(rooms)>50:
        raise ValueError('対象ルームは50件以内です。')
    seen=set()
    for room in rooms:
        if not isinstance(room,dict) or set(room) != {'name','enabled','collection_method','pc_history_download'}:
            raise ValueError('ルーム設定が不正です。')
        name=room['name']
        if not isinstance(name,str) or not name.strip() or name!=name.strip() or len(name)>150 or name=='LINE' or any(unicodedata.category(c).startswith('C') and c!='\u200d' for c in name):
            raise ValueError('ルーム名はLINEに表示される全文を入力してください。')
        key=unicodedata.normalize('NFC',name)
        if key in seen: raise ValueError('同じルームが重複しています。')
        seen.add(key)
        if type(room['enabled']) is not bool or type(room['pc_history_download']) is not bool:
            raise ValueError('ルームの有効状態が不正です。')
        if room['collection_method'] not in ('export','screen_copy','manual') or room['pc_history_download'] != (room['collection_method']=='export'):
            raise ValueError('取得方法と履歴ダウンロード設定が一致しません。')
        if room['collection_method']=='manual' and room['enabled']:
            raise ValueError('スマホ取込専用ルームは自動取得を無効にしてください。')
    return data

def ensure_home():
    (DATA_ROOT/'state').mkdir(parents=True,exist_ok=True)
    path=DATA_ROOT/'collector-config.json'
    try:
        with path.open('x',encoding='utf8') as f: json.dump(DEFAULT,f,ensure_ascii=False,indent=2)
    except FileExistsError: pass

def load_config():
    ensure_home()
    return validate_config(json.loads((DATA_ROOT/'collector-config.json').read_text(encoding='utf-8-sig')))

def revision(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def save_config(data, expected):
    from runtime_lock import WorkerLock
    validate_config(data)
    with WorkerLock(DATA_ROOT/'state/posting-worker'), WorkerLock(DATA_ROOT/'state/collector-worker'):
        if expected != revision(load_config()): raise ValueError('設定が更新されています。画面を再読み込みしてください。')
        path=DATA_ROOT/('config-'+uuid.uuid4().hex+'.tmp')
        try:
            with path.open('w',encoding='utf8') as f:
                json.dump(data,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
            os.replace(path,DATA_ROOT/'collector-config.json')
        finally:
            if path.exists():path.unlink()
    return revision(data)
