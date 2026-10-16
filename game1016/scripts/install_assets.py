"""Prepare pinned legacy coffee assets inside game1016 (no shared symlinks)."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
from zipfile import ZipFile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'vendor/RLDX-1/external_dependencies/robocasa/robocasa/models/assets'
REV = '69f7446f5f0f763971cabeb8b77026fcecced17c'
ARCHIVES = {'fixtures': 'bd993f47d0ae5f5f3d89cf5f9fc28d55ceb878359c49136d30052de0bb1ef231', 'textures': 'e6e9fe1ae4dfdaeb7fe037859926d187c3cdaabdca7afdaa542ed82b943e318c'}
ready = ROOT / 'vendor/assets-ready.json'
if ready.exists() and json.loads(ready.read_text()).get('revision') == REV:
    print('Pinned assets already prepared')
    raise SystemExit(0)
for name, digest in ARCHIVES.items():
    path = ROOT / f'vendor/{name}-v2.zip'
    if not path.exists():
        url = f'https://huggingface.co/datasets/Livfour/robocasa-assets/resolve/{REV}/{name}.zip'
        print(f'Downloading {name} from pinned mirror', flush=True)
        urllib.request.urlretrieve(url, path)
    hasher = hashlib.sha256()
    with path.open('rb') as f:
        while chunk := f.read(1024*1024): hasher.update(chunk)
    if hasher.hexdigest() != digest:
        raise RuntimeError(f'Asset checksum mismatch: {path}; remove this incomplete archive and rerun setup')
    with ZipFile(path) as archive:
        for info in archive.infolist():
            if not (BASE / info.filename).resolve().is_relative_to(BASE.resolve()):
                raise RuntimeError('Unsafe asset archive member')
        archive.extractall(BASE)
subprocess.run([sys.executable, str(ROOT/'scripts/fetch_mugs.py')], check=True)
# Source-tracked XML/configs remain exactly the RLDX revision; archives fill meshes/textures.
subprocess.run(['git','-C',str(ROOT/'vendor/RLDX-1'),'restore','external_dependencies/robocasa/robocasa/models/assets'], check=True)
ready.write_text(json.dumps({'revision': REV, 'archives': ARCHIVES, 'mugs': 'mug-assets-manifest.json'}, indent=2)+'\n')
print('Pinned assets ready')
