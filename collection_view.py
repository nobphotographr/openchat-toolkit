"""Collection health shared by home, operations and room details."""
from datetime import datetime, timezone
import json
from collection_schedule import JST, today_state
from collection_fallback import screen_fallback
import screen_resume
import collection_retry

COMPLETE={'imported','unchanged','screen_imported','screen_unchanged'}
REVIEW={'stopped','screen_resume_review','screen_gap','screen_partial','error'}
LABELS={'complete':'取得完了','pending':'取得途中','review':'要確認','missing':'未取得','disabled':'停止中'}


def build(archive, config, current=None):
    current=current or datetime.now(timezone.utc)
    db=archive.db
    counts={r['room']:r for r in archive.status()}
    rows=[]
    collection_retry.initialize(db)
    for policy in config['rooms']:
        if policy.get('test'):continue
        room=policy['name'];state=screen_resume.job(db,room)
        last=db.execute('SELECT status,started_at,reason FROM attempts WHERE room=? ORDER BY id DESC LIMIT 1',(room,)).fetchone()
        code=last[0] if last else 'never'
        is_today=bool(last and datetime.fromisoformat(last[1]).astimezone(JST).date()==current.astimezone(JST).date())
        health='missing'
        if state and state['phase']=='review' or code in REVIEW:health='review'
        elif state or code in ('running','interrupted','screen_pending','deferred'):health='pending'
        elif is_today and code in COMPLETE and last[2]!='screen_resume_previous_day_completed':health='complete'
        elif is_today and code in ('screen_baseline','screen_baseline_limited'):health='pending'
        active=config.get('enabled',False) and policy['enabled'] and policy.get('collection_method')!='manual'
        if not active:health='disabled'
        row=counts.get(room,dict(room=room,records=0,latest_message=None,latest_screen_observation=None))
        screen=policy.get('collection_method')=='screen_copy'
        fallback=screen_fallback(db,room)
        row.update(method='スマホ履歴の取込専用' if policy.get('collection_method')=='manual' else '画面から新着を取得' if screen or fallback else '履歴ファイルを取得',
                   last_status=code,last_checked=last[1] if last else None,
                   today_status=today_state(db,room,current),health=health,health_label=LABELS[health],can_retry=False)
        if state:row['resume_progress']=dict(buffered_texts=len(json.loads(state['messages'])),phase=state['phase'])
        if health=='review':
            row.update(cause='前回の保存済み履歴まで、本文のつながりを確認できていません。',
                       next_action='不明な部分を飛ばさず停止しています。スマホの履歴ファイルで補完するか、読み取れない箇所の確認が必要です。')
            if state:
                chunk=db.execute('SELECT payload,decision FROM screen_resume_chunks WHERE room=? AND job_id=? ORDER BY id DESC LIMIT 1',(room,state['job_id'])).fetchone()
                if chunk:
                    payload=json.loads(chunk[0]);skips=payload.get('skip_reasons',{})
                    if any(v for k,v in skips.items() if k.startswith(('copy_','row_menu'))):
                        row['cause']='一部の本文でコピー操作を確認できず、続きの取得を停止しました。'
                    elif chunk[1]=='buffer_limit':row['cause']='1回の補完で保持する本文量の上限に達しました。'
                row['can_retry']=bool(active and collection_retry.eligible(db,room))
                if row['can_retry']:
                    row['next_action']='PCのロックを解除し、LINEで対象の本文をコピーできる状態にしてから「同じ位置から1回再試行」を選んでください。保存した本文と位置は保持します。'
            elif code=='stopped':
                row.update(cause='前回の取得操作が完了しませんでした。',next_action='LINEのログイン状態、対象ルーム、開いたままのダイアログを確認してください。保存済み履歴は残っています。')
        elif health=='pending':
            row.update(cause='保存済みの履歴とのつながりを確認しながら、分割して取得しています。',next_action='操作可能なPCで順番に再開します。1日あたりの上限に達した場合は翌日に続けます。')
        elif health=='missing':
            row.update(cause='本日分の取得はまだ完了していません。',next_action='設定した取得時刻以降、PCのロック解除後に未取得の部屋から順番に補完します。')
        elif health=='disabled':row.update(cause='この部屋の定期取得は停止中です。',next_action='保存済みの履歴は閲覧できます。')
        else:row.update(cause='本日の取得が完了しました。',next_action='次の定期取得を待ちます。')
        request=db.execute('SELECT state,requested_at FROM collection_retry_requests WHERE room=?',(room,)).fetchone()
        row['retry_requested']=bool(request and request[0]=='requested' and (current-datetime.fromisoformat(request[1])).total_seconds()<86400)
        rows.append(row)
    order={'review':0,'pending':1,'missing':2,'complete':3,'disabled':4}
    rows.sort(key=lambda r:order[r['health']])
    summary={key:sum(r['health']==key for r in rows) for key in LABELS}
    summary['total']=len([r for r in rows if r['health']!='disabled'])
    return dict(summary=summary,rooms=rows)
