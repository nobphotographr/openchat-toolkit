"""Strict iOS LINE text import; message contents are always treated as data."""
import csv
from datetime import date, datetime
import io
import re
import unicodedata

DAY = re.compile(r'^(\d{4})/(\d{2})/(\d{2})\([月火水木金土日]\)$')
TIME = re.compile(r'^(\d{2}):(\d{2})$')


def parse_mobile(data, expected_room):
    from collector import InvalidExport, MAX_BYTES
    if not data or len(data)>MAX_BYTES:
        raise InvalidExport('export_size_invalid')
    try:
        text=data.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise InvalidExport('utf8_required') from None
    if '\x00' in text:
        raise InvalidExport('nul_in_export')
    try:
        rows=list(csv.reader(io.StringIO(text,newline=''),delimiter='\t',strict=True))
    except csv.Error:
        raise InvalidExport('mobile_quoted_text_invalid') from None
    expected='[LINE] '+expected_room+'のトーク'
    if not rows or len(rows[0])!=1 or unicodedata.normalize('NFC',rows[0][0])!=unicodedata.normalize('NFC',expected):
        raise InvalidExport('mobile_room_header_mismatch')
    if len(rows)<2 or len(rows[1])!=1 or not rows[1][0].startswith('保存日時：'):
        raise InvalidExport('mobile_saved_at_missing')
    try:
        saved_at=datetime.strptime(rows[1][0][5:],'%Y/%m/%d %H:%M')
    except ValueError:
        raise InvalidExport('mobile_saved_at_invalid') from None
    records=[]
    current=None
    for row in rows[2:]:
        if not row:continue
        day=DAY.fullmatch(row[0]) if len(row)==1 else None
        if day:
            try:new_date=date(*map(int,day.groups()))
            except ValueError:raise InvalidExport('mobile_date_invalid') from None
            if (current is not None and new_date<current) or new_date>saved_at.date():
                raise InvalidExport('mobile_date_out_of_range')
            current=new_date
            continue
        match=TIME.fullmatch(row[0])
        if len(row)!=3 or not match or current is None or int(match[1])>23 or int(match[2])>59:
            raise InvalidExport('mobile_row_invalid')
        stamp=f'{current.isoformat()}T{match[1]}:{match[2]}:00+09:00'
        # Author/body are separable in this format. Keep the delimiter instead
        # of guessing equivalence to ambiguous Windows text exports.
        raw=row[1]+'\t'+row[2].replace('\r\n','\n').replace('\r','\n')
        records.append((stamp,raw))
    if not records:raise InvalidExport('no_message_records')
    return records
