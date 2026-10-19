"""Unittest entrypoint that refuses subprocesses capable of operating LINE."""
from pathlib import Path
import sys
import subprocess
import unittest

ROOT=Path(__file__).resolve().parent
ALLOWED={(ROOT/'posting-native'/name).resolve() for name in ('ImageCodec.exe','ImageMatch.exe')}

def guard(event,args):
    if event=='subprocess.Popen':
        executable=args[0]
        # Windows passes executable=None and an already quoted command line.
        if executable is None:
            command=args[1]
            allowed=isinstance(command,str) and any(command==subprocess.list2cmdline([str(p)]) or command.startswith(subprocess.list2cmdline([str(p)])+' ') for p in ALLOWED)
        else:
            allowed=isinstance(executable,str) and Path(executable).resolve() in ALLOWED
        if not allowed:
            raise RuntimeError('offline_suite_blocks_unapproved_subprocess')

def main():
    sys.addaudithook(guard)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(ROOT)))
    return 0 if result.wasSuccessful() else 1

if __name__=='__main__':raise SystemExit(main())
