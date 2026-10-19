"""Run one explicitly approved reservation under shared LINE exclusion locks."""
import json
import sys
from pathlib import Path
from posting import ROOT, Posts, dispatch, now
from runtime_lock import WorkerLock, Refused, desktop_lock_path
from settings import ensure_home
from session_probe import probe


def run():
    ensure_home()
    try:
        with WorkerLock(ROOT/'state/posting-worker'):
            store=Posts()
            try:
                store.recover()
                from recurrence import materialize
                materialize(store)
                if store.paused(): return {'status':'paused','send_invocations':0}
                if not probe().get('ready'): return {'status':'desktop_unavailable','send_invocations':0}
                with WorkerLock(ROOT/'state/collector-worker'):
                    with WorkerLock(desktop_lock_path()):
                        from posting_adapter import Adapter
                        return dispatch(store,Adapter())
            finally: store.close()
    except Refused: return {'status':'busy','send_invocations':0}


def main():
    try: result=run()
    except Exception as exc: result={'status':'error','reason':type(exc).__name__,'send_invocations':0}
    result['observed_at']=now().isoformat()
    # The process lock protects DB recovery; atomic replace protects the status file.
    import uuid
    path=ROOT/'state'/('posting-status-'+uuid.uuid4().hex+'.tmp')
    path.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8');path.replace(ROOT/'state/posting-status.json')
    if sys.stdout: print(json.dumps(result,ensure_ascii=False))
    return 2 if result['status']=='error' else 0


if __name__=='__main__': raise SystemExit(main())
