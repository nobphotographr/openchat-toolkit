"""Read-only operational view. Never import workers, credentials or LINE adapters."""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from posting import ROOT, now


LABELS = {
    'idle':'待機中','not_due':'次の取得を待機中','waiting_for_event':'集計の到着待ち',
    'already_posted':'当日分を登録済み','outside_delivery_window':'翌朝の送信を待機中',
    'hold':'保留モード','paused':'一時停止中','disabled':'停止中','busy':'他の処理の終了待ち',
    'desktop_unavailable':'PCの操作ができる状態を待機中','stopped':'停止・要確認',
    'error':'エラー','needs_review':'要確認・自動再送停止','local_confirmed':'LINE画面で表示確認',
    'local_post_verified':'LINE画面で表示確認','confirmed':'確認済み',
    'manually_confirmed':'手動で確認済み','scheduled':'予約中','pending':'投稿待ち','waiting':'投稿待ち',
    'sending':'送信操作中','operation_done':'送信操作済み・確認待ち',
    'expired':'期限切れ','abandoned':'確認終了・送信中止','preparing':'送信準備中',
    'send_intent':'送信操作中','blocked':'準備で停止','cancelled':'取消済み',
    'review_closed':'確認終了・再送なし','imported':'履歴を保存済み','unchanged':'新着なし',
    'screen_imported':'新着本文を保存済み','screen_unchanged':'新着なし',
    'screen_pending':'続きの取得待ち','screen_resume_review':'要確認：再開位置の確認が必要','deferred':'PCの操作ができる状態を待機中','screen_gap':'取得の連続性を要確認','screen_partial':'一部の本文を未取得',
    'screen_baseline':'新着監視の基準を作成','screen_baseline_limited':'直近の本文から監視開始',
    'running':'取得中','interrupted':'前回の処理が中断',
}


def read_database(path, sql):
    # mode=ro also prevents silently creating a missing database.
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        db.row_factory=sqlite3.Row
        return [dict(row) for row in db.execute(sql)]


def heartbeat(path, current):
    result={'state':'unknown','label':'実行記録なし','observed_at':None,'freshness':'unknown'}
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        stamp=datetime.fromisoformat(value['observed_at'])
        if stamp.tzinfo is None: raise ValueError('timestamp_without_timezone')
        age=(current-stamp).total_seconds()
        state=value.get('status','unknown')
        result.update(state=state if state in LABELS else 'unknown',label=LABELS.get(state,'実行状態を確認できません'),observed_at=stamp.isoformat(),
                      freshness='fresh' if -30<=age<=180 else 'stale')
        # Strict field allowlist; no reasons, raw logs, payloads or credential data.
        if isinstance(value.get('next_delivery_at'),str):
            result['next_delivery_at']=datetime.fromisoformat(value['next_delivery_at']).isoformat()
    except (OSError,ValueError,KeyError,TypeError): pass
    return result


def overview(state=None, current=None):
    state=Path(state or ROOT/'state')
    current=current or now()
    services=[]
    for key,title,path in [('posting','予約投稿',state/'posting-status.json'),
                           ('collector','トーク履歴の取得',state/'collector-status.json')]:
        services.append(dict(key=key,title=title,**heartbeat(path,current)))
    result=dict(observed_at=current.isoformat(),services=services,posts=[],attempts=[],unavailable=[])
    queries={
        'posts':(state/'posting.sqlite3',
            "SELECT id,room,due_at,status,updated_at FROM posts WHERE status!='draft' ORDER BY updated_at DESC LIMIT 30"),
        'attempts':(state/'history.sqlite3',
            'SELECT room,started_at,finished_at,status FROM attempts ORDER BY id DESC LIMIT 30'),
    }
    for key,(path,sql) in queries.items():
        try: result[key]=read_database(path,sql)
        except (sqlite3.Error,OSError): result['unavailable'].append(key)
    result['labels']=LABELS
    return result
