"""Validate the source publication allowlist; never print matched private data."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

ROOT=Path(__file__).resolve().parent
SUFFIXES={'.py','.cs','.js','.cjs','.json','.md','.ps1','.cmd','.html','.css','.svg','.png','.ico','.yml'}
DENIED_DIRS={'state','.venv','__pycache__','evidence','test-output','.git','dist'}

def files(root=ROOT):
    names=json.loads((root/'release-files.json').read_text(encoding='utf8'))
    if not isinstance(names,list) or not names or len(names)!=len(set(names)):
        raise ValueError('invalid_release_manifest')
    found=[]
    for name in names:
        p=PurePosixPath(name)
        if not isinstance(name,str) or p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:
            raise ValueError('unsafe_release_path')
        if any(part in DENIED_DIRS for part in p.parts) or p.name=='collector-config.json':
            raise ValueError('runtime_file_in_manifest')
        if p.suffix not in SUFFIXES and name!='.gitignore':raise ValueError('unsupported_release_type')
        path=root/name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('missing_or_external_release_file')
        data=path.read_bytes()
        if p.suffix not in ('.png','.ico'):
            text=data.decode('utf-8-sig')
            # This is an additional guard, not a replacement for source review.
            checks=[r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                    r'"private_key"\s*:',r'https://hooks\.slack\.com/services/',
                    r'gh[pousr]_[A-Za-z0-9]{25,}',r'github_pat_[A-Za-z0-9_]{30,}',
                    r'[A-Za-z]:[\\/]+Users[\\/]+[^<>\s\\/]+',
                    r'[A-Za-z0-9._-]+@[A-Za-z0-9.-]+\.iam\.gserviceaccount\.com']
            if any(re.search(pattern,text) for pattern in checks):
                raise ValueError('sensitive_content_detected_in_release_file: '+name)
        found.append((name,path))
    return found

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--zip',type=Path,help='Write a new source-only archive outside this repository')
    args=parser.parse_args();entries=files()
    if args.zip:
        target=args.zip.resolve()
        if target.is_relative_to(ROOT.resolve()):raise ValueError('archive_must_be_outside_repository')
        # Exclusive creation prevents replacing an existing review artifact.
        with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED) as out:
            for name,path in entries:out.write(path,'openchat-toolkit/'+name)
    print(json.dumps(dict(status='passed',source_files=len(entries),archive_created=bool(args.zip))))

if __name__=='__main__':main()
