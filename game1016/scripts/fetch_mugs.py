"""Fetch only the contiguous mug entries from the pinned Objaverse ZIP.

HTTP ranges avoid downloading two gigabytes for a coffee-only demo. Every file
is decompressed and CRC checked against the ZIP central directory.
"""
from pathlib import Path
import hashlib
import json
import struct
import zlib
import requests
from remotezip import RemoteZip

ROOT = Path(__file__).resolve().parents[1]
REV = '69f7446f5f0f763971cabeb8b77026fcecced17c'
URL = f'https://huggingface.co/datasets/Livfour/robocasa-assets/resolve/{REV}/objaverse.zip'
BASE = ROOT / 'vendor/RLDX-1/external_dependencies/robocasa/robocasa/models/assets/objects'
with RemoteZip(URL) as archive:
    members = [i for i in archive.infolist() if i.filename.startswith('objaverse/mug/')]
start = min(i.header_offset for i in members)
end = max(i.header_offset + i.compress_size + len(i.filename.encode()) + len(i.extra) + 4096 for i in members)
response = requests.get(URL, headers={'Range': f'bytes={start}-{end}'}, timeout=120)
response.raise_for_status()
if response.status_code != 206:
    raise RuntimeError('Host must support HTTP range requests')
actual_start = int(response.headers['Content-Range'].split(' ')[1].split('-')[0])
block = response.content
report = []
for info in members:
    if info.is_dir():
        continue
    offset = info.header_offset - actual_start
    header = struct.unpack_from('<4s5H3I2H', block, offset)
    if header[0] != b'PK\x03\x04':
        raise ValueError(f'Invalid ZIP header: {info.filename}')
    data_start = offset + 30 + header[-2] + header[-1]
    compressed = block[data_start:data_start + info.compress_size]
    if info.compress_type == 8:
        data = zlib.decompress(compressed, -15)
    elif info.compress_type == 0:
        data = compressed
    else:
        raise ValueError(f'Unsupported compression: {info.compress_type}')
    if len(data) != info.file_size or zlib.crc32(data) != info.CRC:
        raise ValueError(f'CRC mismatch: {info.filename}')
    path = BASE / info.filename
    if not path.resolve().is_relative_to(BASE.resolve()):
        raise ValueError('Unsafe archive path')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    report.append({'path': info.filename, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
(ROOT / 'mug-assets-manifest.json').write_text(json.dumps({'url': URL, 'revision': REV, 'files': report}, indent=2)+'\n')
print(f'Fetched {len(report)} mug files ({len(block):,} network bytes)')
