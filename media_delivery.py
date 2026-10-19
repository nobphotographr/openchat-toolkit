"""Two distinct LINE messages, each intent persisted before its send operation."""
from posting import now
import re


def part(store,ident,name,status):
    with store.db:
        store.db.execute('INSERT OR REPLACE INTO post_parts VALUES(?,?,?,?)',(ident,name,status,now().isoformat()))
        store.audit(ident,status,'part:'+name)


def dispatch_image(store,row,text_adapter,image_factory=None):
    if image_factory is None:
        from media_adapter import ImageAdapter
        image_factory=ImageAdapter
    ident=row['id'];sends=0;phase='preflight';active=None
    try:
        # Never resume partial delivery, even if an accidental caller invokes this twice.
        if store.db.execute('SELECT 1 FROM post_parts WHERE post_id=?',(ident,)).fetchone():
            raise RuntimeError('existing_delivery_parts')
        store.validate(row)
        if row['body'].strip():
            active=text_adapter
            active.preflight(row['room'],row['body']);phase='prepare'
            active.prepare(row['room'],row['body']);active.verify_prepared(row['room'],row['body'])
            part(store,ident,'text','send_intent');store.finish(ident,'send_intent');phase='invoke';sends+=1
            active.invoke(row['room'],row['body'])
            if not active.confirm(row['room'],row['body']):raise RuntimeError('text_confirmation_missing')
            part(store,ident,'text','local_confirmed');active.close();active=None
        phase='image_preflight';active=image_factory(store.path.parent)
        active.preflight(row['room'],row['image_id']);phase='image_prepare'
        active.prepare();active.verify_prepared()
        part(store,ident,'image','send_intent');store.finish(ident,'send_intent');phase='image_invoke';sends+=1
        active.invoke()
        if not active.confirm():raise RuntimeError('image_confirmation_missing')
        part(store,ident,'image','local_confirmed');store.finish(ident,'local_confirmed')
        status='local_confirmed'
    except Exception as error:
        status='blocked' if sends==0 and phase in ('preflight','image_preflight') else 'needs_review'
        # All indeterminate/partial results stop; no part is ever automatically replayed.
        reason=str(error) if re.fullmatch(r'[a-z_0-9]{1,100}',str(error)) else type(error).__name__
        store.finish(ident,status,phase+':'+reason)
    finally:
        for adapter in (active,text_adapter):
            if adapter is not None:
                try:adapter.close()
                except Exception:pass
    return dict(status=status,id=ident,send_invocations=sends)
