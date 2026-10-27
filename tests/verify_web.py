"""Compare browser output with Python manifest replay. export_web.py checks replay vs builder.

SP404_APP1=/path/to/original.bin python3 tests/verify_web.py
Optionally set SP404_ZIP to also exercise the browser's ZIP reader.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app1/tools'))
import export_web as ew

original = Path(ew.mi.ORIGINAL).read_bytes()
assert hashlib.sha256(original).hexdigest() == ew.mi.ORIGINAL_SHA256
data = json.loads((ROOT / 'web/patches.json').read_text())
expected = [dict(ids=ids, hash=hashlib.sha256(ew.apply(original, data, ids)).hexdigest())
            for ids in ew.combinations(data)]
env = dict(os.environ, SP404_APP1=ew.mi.ORIGINAL)
subprocess.run(['node', str(ROOT / 'tests/web.mjs')], input=json.dumps(expected),
               text=True, env=env, check=True)
