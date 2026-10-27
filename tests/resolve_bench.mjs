// Run the pattern resolver (patcher.js resolve) over other APP1 releases and report what it finds.
// The releases stay local: node tests/resolve_bench.mjs path/to/SP404MKII_APP1.bin ...
// (older releases only test robustness; a version is supported only after a device test).
import fs from 'node:fs';
import {applyPatches, resolve} from '../web/patcher.js';

const data = JSON.parse(fs.readFileSync(new URL('../web/patches.json', import.meta.url)));
const verbose = process.argv.includes('-v');
for (const path of process.argv.slice(2).filter(a => a !== '-v')) {
  const img = new Uint8Array(fs.readFileSync(path));
  let r;
  try {
    r = resolve(img, data);
  } catch (e) {
    console.log(`${path}: ${e.message}`);
    continue;
  }
  const missing = Object.entries(r.anchors.missing);
  const ok = r.patches.filter(p => p.ok).map(p => p.id);
  console.log(`${path}\n  anchors found ${Object.keys(r.anchors.found).length}/${data.resolve.anchors.length}; ` +
              `patches: ${ok.join(', ') || 'none'}`);
  for (const p of r.patches.filter(p => !p.ok)) {
    console.log(`  - ${p.id}: ${p.problems.length} problem(s)` + (verbose ? '\n      ' + p.problems.join('\n      ') : ''));
  }
  if (verbose) for (const [n, why] of missing) console.log(`    ${n}: ${why}`);
  if (ok.length) {
    const groups = new Set();
    const ids = ok.filter(id => { const g = data.patches.find(p => p.id === id).group; return !g || !groups.has(g) && groups.add(g); });
    applyPatches(img, r.plan, ids);            // throws if the plan doesn't apply
  }
}
