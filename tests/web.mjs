// Run via python3 tests/verify_web.py; firmware and expectations stay local.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {applyPatches, identify, selectionProblem, readFirmware, resolve, sha256} from '../web/patcher.js';
const data = JSON.parse(fs.readFileSync(new URL('../web/patches.json', import.meta.url)));
const original = new Uint8Array(fs.readFileSync(process.env.SP404_APP1));
const expected = JSON.parse(fs.readFileSync(0, 'utf8'));
assert.ok(await identify(original, data));
assert.deepEqual((await readFirmware(original, 'SP404MKII_APP1.bin')).bytes, original);
const before = await sha256(original);
for (const {ids, hash} of expected) {
  assert.equal(selectionProblem(data, ids), null);
  assert.equal(await sha256(applyPatches(original, data, ids)), hash, ids.join(','));
}
assert.equal(await sha256(original), before, 'source firmware must remain unchanged');
// The pattern resolver, as if v5.52 were unknown, must find every address and build the same images
const resolved = resolve(original, data);
assert.deepEqual(resolved.anchors.missing, {});
assert.deepEqual(resolved.patches.filter(p => !p.ok), []);
for (const {ids, hash} of expected) {
  assert.equal(await sha256(applyPatches(original, resolved.plan, ids)), hash, 'resolved: ' + ids.join(','));
}
assert.ok(selectionProblem(data, ['scale-pads', 'scale-pads-wide']));
assert.ok(selectionProblem(data, ['unknown']));
assert.ok(selectionProblem(data, []));
const changed = original.slice();
changed[data.patches[0].changes[0].offset] ^= 1;
assert.equal(await identify(changed, data), null);
assert.throws(() => applyPatches(changed, data, [data.patches[0].id]));
if (process.env.SP404_ZIP) {
  const zip = new Uint8Array(fs.readFileSync(process.env.SP404_ZIP));
  assert.deepEqual((await readFirmware(zip, 'firmware.zip')).bytes, original);
}
console.log(`Browser/Python outputs match for ${expected.length} selections, by hash and by pattern; input and rejection checks passed.`);
