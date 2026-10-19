from pathlib import Path

class Refused(Exception): pass

class WorkerLock:
    """OS releases the byte lock on crash. Never delete another process's lock."""
    def __init__(self,path): self.path=Path(str(path)+'.lock')
    def __enter__(self):
        import msvcrt
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=self.path.open('a+b');self.file.seek(0,2)
        if self.file.tell()==0:self.file.write(b'0');self.file.flush()
        self.file.seek(0)
        try:msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            self.file.close();raise Refused('別の取得または設定変更が実行中です。') from None
        return self
    def __exit__(self,*args):
        import msvcrt
        try:self.file.seek(0);msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
        finally:self.file.close()


def desktop_lock_path():
    """One UI worker across toolkit copies for the same Windows user."""
    import os
    return Path(os.environ['LOCALAPPDATA'])/'OpenChatToolkit'/'desktop-worker'
