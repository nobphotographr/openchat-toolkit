"""One daily round after its start time, plus bounded continuation of that round."""
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
RETRY_SECONDS = 300
MAX_CONTINUATIONS_PER_DAY = 12
ENVIRONMENT_REASONS = {'session_not_active', 'input_desktop_unavailable',
                       'secure_or_other_desktop', 'session_state_unavailable'}


def today_state(db, room, current):
    day=current.astimezone(JST).replace(hour=0,minute=0,second=0,microsecond=0)
    row=db.execute('SELECT started_at,status,reason FROM attempts WHERE room=? ORDER BY id DESC LIMIT 1',(room,)).fetchone()
    if not row or datetime.fromisoformat(row[0])<day: return '本日未取得'
    if row[2]=='screen_resume_previous_day_completed': return '前回分を補完済み・本日分の取得待ち'
    if row[1] in ('imported','unchanged','screen_imported','screen_unchanged'):
        return '本日取得済み'
    if row[1] in ('screen_baseline','screen_baseline_limited'): return '本日、監視の基準を保存'
    if row[1]=='screen_pending': return '取得途中・続きの取得待ち'
    if row[1] in ('running','interrupted','deferred'): return '取得中・再開待ち'
    return '取得未完了・要確認'


def select_due(archive, config, current):
    from collection_retry import due
    requested=due(archive.db,config,current)
    if requested:return requested
    daily = config.get('daily_schedule')
    day_start = None
    if daily is not None:
        if daily.get('timezone') != 'Asia/Tokyo' or current.utcoffset() is None:
            raise ValueError('invalid_daily_timezone')
        hour, minute = map(int, daily['time'].split(':'))
        duration = daily['window_minutes']
        if not (0 <= hour <= 23 and 0 <= minute <= 59) or type(duration) is not int or not 1 <= duration <= 120:
            raise ValueError('invalid_daily_window')
        local = current.astimezone(JST)
        if local < local.replace(hour=hour, minute=minute, second=0, microsecond=0):
            return None
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    continuations = []
    for room in config['rooms']:
        if not room['enabled'] or room.get('test') or room.get('collection_method') == 'manual':
            continue
        name = room['name']
        last = archive.db.execute('SELECT started_at,status,reason FROM attempts WHERE room=? ORDER BY id DESC LIMIT 1', (name,)).fetchone()
        stamp = datetime.fromisoformat(last[0]) if last else None
        if day_start is None:
            if stamp is None or (current-stamp).total_seconds() >= config['interval_seconds']:
                return name
            continue
        # Missed days collapse into one current-day acquisition, never one replay
        # for every day the PC was off. Manual success today counts as acquired.
        if stamp is None or stamp < day_start:
            return name
        if (current-stamp).total_seconds() < RETRY_SECONDS:
            continue
        resumable = last[1] in ('screen_pending', 'interrupted', 'running', 'deferred')
        resumable |= last[2] == 'screen_resume_previous_day_completed'
        resumable |= last[1] == 'stopped' and last[2] in ENVIRONMENT_REASONS
        if resumable:
            count = sum(datetime.fromisoformat(row[0]) >= day_start for row in
                        archive.db.execute('SELECT started_at FROM attempts WHERE room=? AND started_at>=?',
                                           (name, day_start.astimezone(timezone.utc).isoformat())))
            if count < MAX_CONTINUATIONS_PER_DAY:
                continuations.append((stamp, name))
    return min(continuations)[1] if continuations else None
