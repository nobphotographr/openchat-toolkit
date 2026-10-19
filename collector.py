"""Local-only Windows LINE export archive. Never sends or modifies messages."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent
DAY = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2}) [月火水木金土日]曜日$")
MESSAGE = re.compile(r"^([0-2]\d):([0-5]\d) (.+)$")
MAX_BYTES = 50 * 1024 * 1024


class InvalidExport(ValueError):
    pass


def configured_rooms():
    from settings import load_config
    config = load_config()
    names = [r['name'] for r in config['rooms']]
    if len(names) != len(set(names)):
        raise InvalidExport('duplicate_room_configuration')
    return names


def parse_export(data: bytes):
    if not data or len(data) > MAX_BYTES:
        raise InvalidExport('export_size_invalid')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise InvalidExport('utf8_required') from None
    if '\x00' in text:
        raise InvalidExport('nul_in_export')
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    current_date = None
    current = None
    records = []

    def finish():
        if current is not None:
            # Keep sender and body together: Windows uses ambiguous spaces.
            records.append((current[0], '\n'.join(current[1]).rstrip('\n')))

    for line in lines:
        day = DAY.fullmatch(line)
        if day:
            try:
                new_date = date(*map(int, day.groups()))
            except ValueError:
                raise InvalidExport('invalid_date') from None
            if current_date is not None and new_date < current_date:
                raise InvalidExport('dates_out_of_order')
            finish()
            current = None
            current_date = new_date
            continue
        msg = MESSAGE.fullmatch(line)
        if msg:
            if current_date is None or int(msg[1]) > 23:
                raise InvalidExport('invalid_message_timestamp')
            finish()
            current = (f'{current_date.isoformat()}T{msg[1]}:{msg[2]}:00+09:00', [msg[3]])
        elif current is not None:
            current[1].append(line)
        elif line:
            raise InvalidExport('unsupported_export_format')
    finish()
    if current_date is None or not records:
        raise InvalidExport('no_message_records')
    return records


class Archive:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS snapshots (
          room TEXT NOT NULL, sha256 TEXT NOT NULL, raw BLOB NOT NULL,
          captured_at TEXT NOT NULL, source TEXT NOT NULL, record_count INTEGER NOT NULL,
          PRIMARY KEY(room,sha256));
        CREATE TABLE IF NOT EXISTS records (
          room TEXT NOT NULL, fingerprint TEXT NOT NULL, occurrence INTEGER NOT NULL,
          timestamp TEXT NOT NULL, raw_text TEXT NOT NULL, first_seen TEXT NOT NULL,
          PRIMARY KEY(room,fingerprint,occurrence));
        CREATE TABLE IF NOT EXISTS snapshot_records (
          room TEXT NOT NULL, sha256 TEXT NOT NULL, ordinal INTEGER NOT NULL,
          fingerprint TEXT NOT NULL, occurrence INTEGER NOT NULL,
          PRIMARY KEY(room,sha256,ordinal),
          FOREIGN KEY(room,sha256) REFERENCES snapshots(room,sha256),
          FOREIGN KEY(room,fingerprint,occurrence) REFERENCES records(room,fingerprint,occurrence));
        CREATE TABLE IF NOT EXISTS attempts (
          id INTEGER PRIMARY KEY, room TEXT NOT NULL, started_at TEXT NOT NULL,
          finished_at TEXT, status TEXT NOT NULL, reason TEXT);
        CREATE TABLE IF NOT EXISTS exports (
          attempt_id INTEGER PRIMARY KEY, room TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
          receipt TEXT, sha256 TEXT,
          FOREIGN KEY(attempt_id) REFERENCES attempts(id));
        ''')
        from archive_features import initialize
        initialize(self.db)

    def close(self):
        self.db.close()

    def ingest(self, room: str, data: bytes, *, expected_room: str, source='windows_native'):
        result = self._ingest(room, data, expected_room=expected_room, source=source)
        from archive_features import refresh
        refresh(self.db)
        return result

    def _ingest(self, room: str, data: bytes, *, expected_room: str, source='windows_native'):
        if room != expected_room or room not in configured_rooms():
            raise InvalidExport('room_identity_mismatch')
        if source not in {'windows_native', 'windows_manual_test', 'ios_manual'}:
            raise InvalidExport('unsupported_source')
        # Validate before making any database changes.
        if source=='ios_manual':
            from mobile_export import parse_mobile
            records=parse_mobile(data,room)
        else:
            records = parse_export(data)
        digest = hashlib.sha256(data).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with self.db:
            if self.db.execute('SELECT 1 FROM snapshots WHERE room=? AND sha256=?', (room, digest)).fetchone():
                return {'status': 'unchanged', 'records': len(records), 'new_records': 0, 'sha256': digest}
            self.db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?)', (room, digest, data, now, source, len(records)))
            occurrences = Counter()
            inserted = 0
            for ordinal, (timestamp, raw) in enumerate(records):
                identity = ['ios_v1',timestamp,raw] if source=='ios_manual' else [timestamp,raw]
                fp = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode('utf-8')).hexdigest()
                occurrences[fp] += 1
                occurrence = occurrences[fp]
                inserted += self.db.execute('INSERT OR IGNORE INTO records VALUES (?,?,?,?,?,?)',
                    (room, fp, occurrence, timestamp, raw, now)).rowcount
                self.db.execute('INSERT INTO snapshot_records VALUES (?,?,?,?,?)',
                    (room, digest, ordinal, fp, occurrence))
        return {'status': 'imported', 'records': len(records), 'new_records': inserted, 'sha256': digest}

    def status(self):
        return [dict(zip(('room', 'records', 'latest_message','latest_screen_observation'), row)) for row in self.db.execute(
            '''SELECT r.room,COUNT(*),MAX(CASE WHEN s.time_kind IS NULL THEN r.timestamp END),
               MAX(CASE WHEN s.time_kind='observed' THEN r.timestamp END)
               FROM records r LEFT JOIN record_sources s ON s.room=r.room AND s.fingerprint=r.fingerprint AND s.occurrence=r.occurrence
               GROUP BY r.room ORDER BY r.room''')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT/'state/history.sqlite3')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    imp = sub.add_parser('import-test-export')
    imp.add_argument('path', type=Path)
    imp.add_argument('--room', required=True, choices=configured_rooms())
    mobile=sub.add_parser('import-mobile')
    mobile.add_argument('--room',required=True,choices=configured_rooms())
    mobile.add_argument('path',type=Path)
    args = parser.parse_args()
    archive = Archive(args.database)
    try:
        if args.command == 'status':
            counts = {r['room']: r for r in archive.status()}
            result = []
            for room in configured_rooms():
                entry = dict(counts.get(room, {'room':room, 'records':0, 'latest_message':None}))
                last = archive.db.execute('SELECT started_at,finished_at,status,reason FROM attempts WHERE room=? ORDER BY id DESC LIMIT 1', (room,)).fetchone()
                entry['last_attempt'] = dict(zip(('started_at','finished_at','status','reason'),last)) if last else None
                result.append(entry)
        elif args.command=='import-mobile':
            result=archive.ingest(args.room,args.path.read_bytes(),expected_room=args.room,source='ios_manual')
        else:
            room = args.room
            result = archive.ingest(room, args.path.read_bytes(), expected_room=room, source='windows_manual_test')
        if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))
    finally:
        archive.close()


if __name__ == '__main__':
    main()
