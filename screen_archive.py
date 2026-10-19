"""Archive exact copied text with observation times, not invented message dates."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unicodedata
from collector import InvalidExport, configured_rooms

JST=timezone(timedelta(hours=9))


def digest(body): return hashlib.sha256(body.encode('utf-8')).hexdigest()


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS screen_scans (
      id INTEGER PRIMARY KEY, room TEXT NOT NULL, observed_at TEXT NOT NULL,
      payload TEXT NOT NULL, status TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS screen_checkpoints (
      room TEXT PRIMARY KEY, hashes TEXT NOT NULL, observed_at TEXT NOT NULL,
      sequence INTEGER NOT NULL);
    ''')


def validate(payload, room):
    if not isinstance(payload,dict): raise InvalidExport('screen_contract_invalid')
    if room not in configured_rooms() or payload.get('room')!=room:
        raise InvalidExport('screen_room_mismatch')
    expected={'schema_version':1,'status':'screen_read','bottom_confirmed':True,'send_invocations':0,'history_download_invocations':0}
    if any(type(payload.get(k)) is not type(v) or payload[k]!=v for k,v in expected.items()):
        raise InvalidExport('screen_contract_invalid')
    try:
        observed=datetime.fromisoformat(payload['observed_at'])
        if observed.utcoffset() is None: raise ValueError()
        if abs((datetime.now(timezone.utc)-observed).total_seconds())>300: raise ValueError()
    except (ValueError,TypeError,KeyError): raise InvalidExport('screen_observation_date_invalid') from None
    pages=payload.get('pages')
    if not isinstance(pages,list) or not 1<=len(pages)<=6: raise InvalidExport('screen_pages_invalid')
    for page in pages:
        if not isinstance(page,dict) or not isinstance(page.get('messages'),list) or len(page['messages'])>45: raise InvalidExport('screen_page_invalid')
        for message in page['messages']:
            if not isinstance(message,dict): raise InvalidExport('screen_body_invalid')
            body=message.get('body')
            if not isinstance(body,str) or not 0<len(body)<100000 or '\x00' in body or digest(body)!=message.get('sha256'):
                raise InvalidExport('screen_body_invalid')
    if sum(len(p['messages']) for p in pages)>45: raise InvalidExport('screen_message_limit')
    return observed.astimezone(JST).isoformat()


def combine(pages):
    """Native pages are newest-first. Preserve repetitions within a page."""
    combined=[]; gaps=0; seen={}
    for page in pages:
        items=page['messages']
        if not items: continue
        if all(isinstance(m.get('item_id'),str) and m['item_id'] for m in items):
            # Runtime IDs identify UI elements only during this native invocation.
            # They are never used as message IDs across runs or restarts.
            if combined and not any(item['item_id'] in seen for item in items):
                gaps+=1
            for item in items:
                key=item['item_id']
                if key in seen:
                    if seen[key]!=item['sha256']: raise InvalidExport('screen_row_changed_during_scan')
                else:
                    seen[key]=item['sha256']; combined.append(item)
            continue
        a=[m['sha256'] for m in combined]; b=[m['sha256'] for m in items]
        overlaps=[i for i in range(1,min(len(a),len(b))+1) if a[-i:]==b[:i]]
        if not combined: combined=list(items)
        elif overlaps: combined.extend(items[max(overlaps):])
        else: gaps+=1; combined.extend(items)
    return combined,gaps


def anchor_match(previous, current):
    if previous==current: return 0,'exact'
    anchor=previous[:min(3,len(previous))]
    matches=[i for i in range(len(current)-len(anchor)+1) if current[i:i+len(anchor)]==anchor]
    if len(matches)==1:return matches[0],'exact'
    if matches:return None,None
    # A previously saved post may disappear from LINE. Do not discard its
    # archive record. Require a unique, longer sequence across the absent
    # INTERNAL post: four or more matching texts, both endpoints preserved,
    # at least three distinct hashes, and only one omission. Unknown new text
    # or two missing posts cannot pass this check.
    context=previous[:8]
    candidates=[]
    if len(context)>=5:
        for omitted in range(1,len(context)-1):
            proof=context[:omitted]+context[omitted+1:]
            if len(set(proof))<3:continue
            for i in range(len(current)-len(proof)+1):
                if current[i:i+len(proof)]==proof:candidates.append((i,omitted))
    if len(candidates)==1:return candidates[0][0],'one_saved_text_absent'
    return None,None


def locate_anchor(previous,current):return anchor_match(previous,current)[0]


def ingest(archive, room, payload):
    observed=validate(payload,room); db=archive.db; initialize(db)
    messages,gaps=combine(payload['pages'])
    if not messages: raise InvalidExport('screen_no_messages')
    allowed_skips={'no_text_bubble','row_not_visible','nontext_clipboard','image_attachment','video_attachment'}
    failures={k:v for k,v in payload.get('skip_reasons',{}).items() if k not in allowed_skips and v}
    older_warnings={}
    if failures and all(m.get('item_id') for m in messages):
        # The first run establishes a boundary for NEW messages; it is not a
        # replacement for the phone's historical export. An unreadable older
        # row must not invalidate a completely read newer prefix.
        positions={}; unreadable=[]; copied_ids={m['item_id'] for m in messages}
        for page in payload['pages']:
            for item in page.get('diagnostics',[]):
                ident=item.get('id')
                if ident not in positions: positions[ident]=len(positions)
                if item.get('reason') not in allowed_skips|{'copied'} and ident not in copied_ids:
                    unreadable.append(positions[ident])
        if unreadable and all(m['item_id'] in positions for m in messages):
            cutoff=min(unreadable)
            messages=[m for m in messages if positions[m['item_id']]<cutoff]
            older_warnings=failures; failures={}
    return _store(archive,room,payload,observed,messages,gaps,failures,older_warnings)


def ingest_resumed(archive,room,messages,observed,job_id,expected_checkpoint):
    if room not in configured_rooms() or not 1<=len(messages)<=4000:
        raise InvalidExport('screen_resume_messages_invalid')
    for m in messages:
        if not isinstance(m.get('body'),str) or not 0<len(m['body'])<100000 or digest(m['body'])!=m.get('sha256'):
            raise InvalidExport('screen_resume_messages_invalid')
        stamp=datetime.fromisoformat(m['observed_at'])
        if stamp.utcoffset() is None or stamp>datetime.fromisoformat(observed):
            raise InvalidExport('screen_resume_observation_invalid')
    payload=dict(status='screen_resume_commit',job_id=job_id,messages=messages,observed_at=observed)
    started=archive.db.execute('SELECT observed_at FROM screen_resume_jobs WHERE room=? AND job_id=?',(room,job_id)).fetchone()
    result=_store(archive,room,payload,observed,messages,0,{}, {},job_id,expected_checkpoint)
    result.update(resumed=True,buffered_texts=len(messages),resume_started_at=started[0] if started else observed)
    if datetime.fromisoformat(result['resume_started_at']).astimezone(JST).date()<datetime.now(JST).date():
        result['reason']='screen_resume_previous_day_completed'
    return result


def _store(archive,room,payload,observed,messages,gaps,failures,older_warnings,resume_job_id=None,expected_checkpoint=None):
    db=archive.db
    hashes=[m['sha256'] for m in messages]
    encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
    with db:
        db.execute('BEGIN IMMEDIATE')
        previous=db.execute('SELECT hashes,observed_at,sequence FROM screen_checkpoints WHERE room=?',(room,)).fetchone()
        if resume_job_id is not None:
            if (list(previous) if previous else None)!=expected_checkpoint:
                raise InvalidExport('screen_resume_checkpoint_changed')
            job=db.execute('SELECT job_id FROM screen_resume_jobs WHERE room=?',(room,)).fetchone()
            if not job or job[0]!=resume_job_id: raise InvalidExport('screen_resume_job_changed')
        status='screen_baseline' if previous is None else 'screen_unchanged'
        position,alignment=(anchor_match(json.loads(previous[0]),hashes) if previous and messages else (len(messages) if messages else None,None))
        if previous and observed<=previous[1]:
            position=None; status='screen_old_observation'
        elif position is None: status='screen_gap'
        if gaps:
            position=None; status='screen_gap'
        elif failures:
            position=None; status='screen_partial'
        elif position is None and older_warnings:
            status='screen_partial'
        elif previous is None and older_warnings:
            status='screen_baseline_limited'
        # Save every observation even when continuity/identity cannot be confirmed.
        scan=db.execute('INSERT INTO screen_scans(room,observed_at,payload,status) VALUES (?,?,?,?)',(room,observed,encoded,status)).lastrowid
        added=0; matched=0
        if position is not None:
            sequence=previous[2] if previous else 0
            known_mobile=set()
            if previous is None:
                for (raw,) in db.execute('SELECT raw_text FROM records WHERE room=?',(room,)):
                    if '\t' in raw:
                        known_mobile.add(unicodedata.normalize('NFC',raw.split('\t',1)[1]).strip())
            for item in reversed(messages[:position]):
                # Initial history overlap is preserved in the scan, not re-added.
                if previous is None and unicodedata.normalize('NFC',item['body']).strip() in known_mobile:
                    matched+=1; continue
                sequence+=1
                fingerprint=digest(json.dumps(['screen_copy_v1',room,sequence,item['sha256']],ensure_ascii=False))
                captured=item['observed_at'] if resume_job_id else observed
                db.execute('INSERT INTO records(room,fingerprint,occurrence,timestamp,raw_text,first_seen) VALUES (?,?,1,?,?,?)',(room,fingerprint,captured,item['body'],captured))
                db.execute('INSERT INTO record_sources(room,fingerprint,occurrence,source,time_kind) VALUES (?,?,1,?,?)',(room,fingerprint,'screen_copy','observed'))
                added+=1
            if previous is not None and added: status='screen_imported'
            if previous is None or added or alignment=='one_saved_text_absent':
                db.execute('INSERT OR REPLACE INTO screen_checkpoints VALUES (?,?,?,?)',(room,json.dumps(hashes),observed,sequence))
            db.execute('UPDATE screen_scans SET status=? WHERE id=?',(status,scan))
            if resume_job_id:
                db.execute('DELETE FROM screen_resume_jobs WHERE room=? AND job_id=?',(room,resume_job_id))
    from archive_features import refresh
    refresh(db)
    return {'status':status,'new_records':added,'observed_texts':len(messages),'matched_mobile_history':matched,
            'page_gaps':gaps,'skipped_rows':payload.get('skipped_rows',0),'continuity':'not_confirmed' if position is None else ('initial_baseline' if previous is None else 'text_sequence_matched'),
            'timestamp_kind':'observed','scan_id':scan,'read_failures':failures,'older_unreadable_rows':older_warnings,'boundary_alignment':alignment}
