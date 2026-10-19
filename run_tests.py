"""Run synthetic tests in a fresh source copy, never the user's state directory."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile

def main():
    root=Path(__file__).resolve().parent
    manifest=json.loads((root/'release-files.json').read_text(encoding='utf8'))
    with tempfile.TemporaryDirectory(prefix='openchat-toolkit-tests-') as temp:
        work=Path(temp)
        if work.resolve().parent != Path(tempfile.gettempdir()).resolve():
            raise RuntimeError('unexpected_temporary_workspace')
        for name in manifest:
            dest=work/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/name,dest)
        config=json.loads((work/'collector-config.example.json').read_text(encoding='utf8'))
        config['rooms']=[dict(name=n,enabled=False,collection_method='export',pc_history_download=True) for n in ('サンプルルーム','サンプル投稿先','別のサンプルルーム')]
        (work/'collector-config.json').write_text(json.dumps(config,ensure_ascii=False),encoding='utf8')
        (work/'state').mkdir()
        subprocess.run([sys.executable,'build.py'],cwd=work,check=True)
        subprocess.run([sys.executable,'offline_suite.py'],cwd=work,check=True)
        node=shutil.which('node')
        if node:subprocess.run([node,'--test','test_web_state.cjs'],cwd=work,check=True)
        else:print('Node.js is unavailable: frontend regression tests were NOT run.')

if __name__=='__main__':main()
