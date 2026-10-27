// The patcher's core, without any page code (app.js has that), so it also runs in Node for
// testing. Everything happens on the user's machine: the firmware is never uploaded.
//
// patches.json comes from the reverse-engineering project's export (app1/tools/export_web.py):
// the firmware it fits (by SHA-256), our code (the payload) and, per patch, every byte it
// changes together with the byte that must be there before. Any mismatch stops the build.

const APP1_NAME = 'SP404MKII_APP1.bin';

// --- zip ---------------------------------------------------------------------------------

function crc32(bytes) {
  let table = crc32.table;
  if (!table) {
    table = crc32.table = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c >>> 0;
    }
  }
  let crc = 0xFFFFFFFF;
  for (let i = 0; i < bytes.length; i++) crc = table[(crc ^ bytes[i]) & 0xFF] ^ (crc >>> 8);
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

async function inflateRaw(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

// The files in a zip: [{name, method, crc, size, compressedSize, localOffset}]
function zipEntries(zip) {
  const view = new DataView(zip.buffer, zip.byteOffset, zip.byteLength);
  let eocd = -1;
  for (let i = zip.length - 22; i >= Math.max(0, zip.length - 22 - 0xFFFF); i--) {
    if (view.getUint32(i, true) === 0x06054B50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('This file is not a zip archive.');
  const count = view.getUint16(eocd + 10, true);
  let p = view.getUint32(eocd + 16, true);
  const entries = [];
  for (let n = 0; n < count; n++) {
    if (view.getUint32(p, true) !== 0x02014B50) throw new Error('The zip archive is damaged.');
    const nameLen = view.getUint16(p + 28, true);
    entries.push({
      name: new TextDecoder().decode(zip.subarray(p + 46, p + 46 + nameLen)),
      method: view.getUint16(p + 10, true),
      crc: view.getUint32(p + 16, true),
      compressedSize: view.getUint32(p + 20, true),
      size: view.getUint32(p + 24, true),
      localOffset: view.getUint32(p + 42, true),
    });
    p += 46 + nameLen + view.getUint16(p + 30, true) + view.getUint16(p + 32, true);
  }
  return entries;
}

async function zipRead(zip, entry) {
  const view = new DataView(zip.buffer, zip.byteOffset, zip.byteLength);
  const p = entry.localOffset;
  if (view.getUint32(p, true) !== 0x04034B50) throw new Error('The zip archive is damaged.');
  const start = p + 30 + view.getUint16(p + 26, true) + view.getUint16(p + 28, true);
  const raw = zip.subarray(start, start + entry.compressedSize);
  let data;
  if (entry.method === 0) data = raw.slice();
  else if (entry.method === 8) data = await inflateRaw(raw);
  else throw new Error(`${entry.name} is compressed in a way this page can't read (method ${entry.method}).`);
  if (data.length !== entry.size || crc32(data) !== entry.crc) throw new Error(`${entry.name} in the zip is damaged.`);
  return data;
}

// --- firmware ----------------------------------------------------------------------------

// Roland's zip (any folder inside it) or the bare SP404MKII_APP1.bin -> {bytes, name}
export async function readFirmware(bytes, fileName) {
  bytes = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const isZip = bytes.length >= 4 && bytes[0] === 0x50 && bytes[1] === 0x4B && bytes[2] === 3 && bytes[3] === 4;
  if (!isZip) return { bytes, name: fileName };
  const entry = zipEntries(bytes).find(e => e.name.split('/').pop().toUpperCase() === APP1_NAME.toUpperCase());
  if (!entry) throw new Error(`There's no ${APP1_NAME} in this zip. Use the system program zip from Roland's SP-404MKII download page.`);
  return { bytes: await zipRead(bytes, entry), name: entry.name };
}

export async function sha256(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
}

// The firmware entry of patches.json this file is, or null
export async function identify(bytes, data) {
  const hash = await sha256(bytes);
  return data.firmware.find(f => f.sha256 === hash && f.size === bytes.length) || null;
}

// --- patching ----------------------------------------------------------------------------

function fromHex(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(2 * i, 2), 16);
  return out;
}

function fromBase64(text) {
  const bin = atob(text);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const hex = bytes => Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');

// Why this selection can't be built, or null
export function selectionProblem(data, ids) {
  if (!ids.length) return 'Choose at least one patch.';
  const seen = {};
  for (const id of ids) {
    const p = data.patches.find(q => q.id === id);
    if (!p) return `Unknown patch ${id}.`;
    if (p.group && seen[p.group]) return `Choose only one of ${seen[p.group]} and ${p.name}.`;
    if (p.group) seen[p.group] = p.name;
  }
  return null;
}

// The patched firmware (a new array). Throws if any original byte isn't what the patch expects.
// Same steps as export_web.py's apply(), which checks them against the Python build.
export function applyPatches(original, data, ids) {
  const problem = selectionProblem(data, ids);
  if (problem) throw new Error(problem);
  const patches = ids.map(id => data.patches.find(p => p.id === id));
  const steps = [];
  let img = original;
  if (patches.some(p => p.payload)) {
    const payload = data.payload.bytes || fromBase64(data.payload.base64);
    const offset = data.payload.offset;
    if (original.length > offset) throw new Error('This firmware is larger than expected; is it already patched?');
    img = new Uint8Array(offset + payload.length).fill(0xFF);
    img.set(original, 0);
    img.set(payload, offset);
    for (const c of data.payload_setup) steps.push(['start-up', c]);
  } else {
    img = original.slice();
  }
  for (const p of patches) for (const c of p.changes) steps.push([p.name, c]);

  const applied = new Map();             // offset -> new bytes (hex), for changes two patches share
  for (const [who, c] of steps) {
    const old = fromHex(c.old), now = fromHex(c.new);
    const found = img.subarray(c.offset, c.offset + old.length);
    if (applied.get(c.offset) === c.new && hex(found) === c.new) continue;
    if (hex(found) !== c.old) {
      throw new Error(`${who}: at file offset 0x${c.offset.toString(16)} the firmware has ${hex(found)}, ` +
                      `expected ${c.old}. Nothing was changed.`);
    }
    img.set(now, c.offset);
    applied.set(c.offset, c.new);
  }
  return img;
}

// --- other versions ----------------------------------------------------------------------
//
// A firmware patches.json doesn't list by SHA-256: find every address the patches and the
// payload use by the rules export_web.py derived from v5.52 (app1/tools/anchors.py), then build
// the same kind of change lists as patches.json has for v5.52. A patch is offered only if all
// its addresses are found and all its original bytes are as expected.
// Not used by the page yet: other versions also need the RAM checks in web-patcher.md.

const u16 = (b, o) => b[o] | b[o + 1] << 8;
const u32 = (b, o) => (b[o] | b[o + 1] << 8 | b[o + 2] << 16 | b[o + 3] << 24) >>> 0;
const half = v => [v & 0xFF, v >> 8 & 0xFF];
const word = v => [v & 0xFF, v >>> 8 & 0xFF, v >>> 16 & 0xFF, v >>> 24];
const addrHex = v => '0x' + (v >>> 0).toString(16).padStart(8, '0');

// The scatter-load table: what __scatterload copies where at start-up
function scatterTable(img, flash) {
  // __scatterload: 'adr r0, rel; ldm r0, {r10, r11}; add r10, r0; add r11, r0'
  const hits = search(img, compile('??a090e8000c82448344'), 2);
  if (hits.length !== 1) throw new Error('The start-up code (__scatterload) was not found.');
  const rel = ((hits[0] + 4) & ~3) + img[hits[0]] * 4;
  const start = rel + u32(img, rel), end = rel + u32(img, rel + 4);
  if (end <= start || (end - start) % 16 || end > img.length) throw new Error('The scatter-load table is damaged.');
  const entries = [];
  for (let o = start; o < end; o += 16) {
    entries.push({ src: u32(img, o), dst: u32(img, o + 4), size: u32(img, o + 8), handler: u32(img, o + 12) });
  }
  const code = entries.find(e => e.dst === 0x80000000);
  if (!code) throw new Error('The main code region was not found.');
  const regions = entries.filter(e => e.handler === code.handler && e.src >= flash)
    .map(e => ({ rt: e.dst, fo: e.src - flash, size: e.size }));
  return { rel, start, end, entries, copy: code.handler, regions };
}

function toFile(regions, addr) {
  const r = regions.find(r => addr >= r.rt && addr < r.rt + r.size);
  return r ? addr - r.rt + r.fo : null;
}

function toRuntime(regions, off) {
  const r = regions.find(r => off >= r.fo && off < r.fo + r.size);
  return r ? off - r.fo + r.rt : null;
}

// 'a1b2??' -> {bytes, fixed}
function compile(text) {
  const n = text.length / 2, bytes = new Uint8Array(n), fixed = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const h = text.substr(2 * i, 2);
    if (h !== '??') { bytes[i] = parseInt(h, 16); fixed[i] = 1; }
  }
  return { bytes, fixed };
}

// Start offsets of up to `limit` matches
function search(img, pat, limit) {
  const { bytes, fixed } = pat;
  let key = -1;                                  // the rarest required byte
  const counts = byteCounts(img);
  for (let i = 0; i < bytes.length; i++) {
    if (fixed[i] && (key < 0 || counts[bytes[i]] < counts[bytes[key]])) key = i;
  }
  const out = [];
  for (let p = img.indexOf(bytes[key], key); p >= 0; p = img.indexOf(bytes[key], p + 1)) {
    const s = p - key;
    if (s + bytes.length > img.length) break;
    let ok = true;
    for (let i = 0; i < bytes.length; i++) {
      if (fixed[i] && img[s + i] !== bytes[i]) { ok = false; break; }
    }
    if (ok && out.push(s) >= limit) break;
  }
  return out;
}

const countCache = new WeakMap();
function byteCounts(img) {
  let c = countCache.get(img);
  if (!c) {
    c = new Uint32Array(256);
    for (let i = 0; i < img.length; i++) c[img[i]]++;
    countCache.set(img, c);
  }
  return c;
}

const movwImm = (h1, h2) => (h1 & 0xF) << 12 | (h1 >> 10 & 1) << 11 | (h2 >> 12 & 7) << 8 | (h2 & 0xFF);

function blTarget(h1, h2, pc) {
  const s = h1 >> 10 & 1, i1 = 1 ^ (h2 >> 13 & 1) ^ s, i2 = 1 ^ (h2 >> 11 & 1) ^ s;
  const imm = s << 24 | i1 << 23 | i2 << 22 | (h1 & 0x3FF) << 12 | (h2 & 0x7FF) << 1;
  return pc + 4 + imm - (s ? 1 << 25 : 0);
}

// The address one 'find' pattern gives at match offset s (anchors.py found_value), or null
function foundValue(img, regions, c, s) {
  const o = s + c.k;
  let v;
  if (c.how === 'at') {
    v = toRuntime(regions, o);
    if (v === null) return null;
  } else if (c.how === 'word') {
    v = u32(img, o);
  } else if (c.how === 'bl') {
    const h1 = u16(img, o), h2 = u16(img, o + 2), pc = toRuntime(regions, o);
    if ((h1 & 0xF800) !== 0xF000 || (h2 & 0xD000) !== 0xD000 || pc === null) return null;
    v = blTarget(h1, h2, pc);
  } else {
    const t = s + c.k2;
    const w1 = u16(img, o), w2 = u16(img, o + 2), t1 = u16(img, t), t2 = u16(img, t + 2);
    if ((w1 & 0xFBF0) !== 0xF240 || (t1 & 0xFBF0) !== 0xF2C0 || (w2 >> 8 & 0xF) !== (t2 >> 8 & 0xF)) return null;
    v = movwImm(t1, t2) * 0x10000 + movwImm(w1, w2);
  }
  return (v + c.add) >>> 0;
}

// {name: address} for every anchor found, {name: why not} for the others
function findAnchors(img, regions, rules) {
  const found = {}, missing = {};
  for (const r of rules) {
    const base = r.from ?? r.load ?? r.wordof;
    if (base !== undefined && !(base in found)) { missing[r.name] = `needs ${base}`; continue; }
    if (r.from !== undefined) {
      found[r.name] = (found[base] + r.add) >>> 0;
    } else if (r.load !== undefined) {
      const o = toFile(regions, found[base]);
      if (o === null) missing[r.name] = `${base} is not in the image`;
      else found[r.name] = u32(img, o);
    } else if (r.wordof !== undefined) {
      const want = (found[base] + (r.add || 0)) >>> 0, hits = [];
      for (const g of regions) {
        for (let o = g.fo; o + 4 <= g.fo + g.size; o += 4) if (u32(img, o) === want) hits.push(o - g.fo + g.rt);
      }
      if (hits.length === 1) found[r.name] = hits[0];
      else missing[r.name] = `${hits.length} words hold ${addrHex(want)}`;
    } else {
      const values = new Set();
      for (const c of r.find) {
        const hits = search(img, compile(c.p), 2);
        if (hits.length !== 1) continue;
        const v = foundValue(img, regions, c, hits[0]);
        values.add(v === null ? 'invalid' : v);
      }
      if (values.size === 1 && !values.has('invalid')) found[r.name] = [...values][0];
      else missing[r.name] = values.size ? 'its patterns disagree' : 'no pattern matches';
    }
  }
  return { found, missing };
}

function movwMovt(top, rd, imm16) {
  const h1 = (top ? 0xF2C0 : 0xF240) | (imm16 >> 11 & 1) << 10 | imm16 >> 12;
  return [...half(h1), ...half((imm16 >> 8 & 7) << 12 | rd << 8 | imm16 & 0xFF)];
}

// A template's parts at runtime address `here` (anchors.py encode): bytes, null for '??'
function encode(parts, found, here) {
  const value = e => {
    if (typeof e === 'number') return e;
    const base = e[0] === '@' ? here : found[e[0]];
    if (base === undefined) throw new Error(`${e[0]} was not found`);
    return (base + e[1]) >>> 0;
  };
  const out = [];
  for (const part of parts) {
    const [kind, arg] = Object.entries(part)[0];
    const pos = here + out.length;
    if (kind === 'hex') {
      for (let i = 0; i < arg.length; i += 2) out.push(arg.substr(i, 2) === '??' ? null : parseInt(arg.substr(i, 2), 16));
    } else if (kind === 'word') {
      out.push(...word(value(arg)));
    } else if (kind === 'mov32') {
      const v = value(arg[1]);
      out.push(...movwMovt(false, arg[0], v & 0xFFFF), ...movwMovt(true, arg[0], v >>> 16));
    } else if (kind === 'tbh') {
      const d = value(arg[0]) - value(arg[1]);
      if (d % 2 || d < 0 || d >= 0x20000) throw new Error(`a switch entry can't reach ${d} bytes`);
      out.push(...half(d / 2));
    } else if (kind === 'ldrpc') {
      const target = value(arg), imm = target - (((pos + 4) & ~3) >>> 0);
      if (target % 4 || imm <= -0x1000 || imm >= 0x1000) throw new Error(`'ldr.w pc' at ${addrHex(pos)} can't reach ${addrHex(target)}`);
      out.push(...half(imm >= 0 ? 0xF8DF : 0xF85F), ...half(0xF000 | Math.abs(imm)));
    } else {
      throw new Error(`unknown template part ${kind}`);
    }
  }
  return out;
}

// The start-up change for a payload of `size` bytes: the scatter-load table copied with one
// more entry (make_image.py add_payload_copy)
function payloadSetup(img, table, data, size) {
  const r = data.resolve, at = r.scatter_table, flash = r.flash;
  const old = img.subarray(table.start, table.end);
  const added = [...word(flash + data.payload.offset), ...word(r.payload_ram), ...word(size), ...word(table.copy)];
  const rel = table.rel;
  if (img.length > at || at + old.length + 16 > data.payload.offset) throw new Error('No free flash for the start-up table.');
  const relOld = img.subarray(rel, rel + 8);
  const relNew = [...word(at - rel), ...word(at + old.length + 16 - rel)];
  return [
    { offset: rel, old: hex(relOld), new: hex(relNew), why: 'start-up: copy the payload to SDRAM' },
    { offset: at, old: 'ff'.repeat(old.length + 16), new: hex(old) + hex(added), why: 'start-up: copy the payload to SDRAM' },
  ];
}

// For a firmware not listed by hash: {plan (use like patches.json with applyPatches), patches:
// [{id, name, ok, problems}], anchors: {found, missing}}
export function resolve(original, data) {
  const r = data.resolve;
  const table = scatterTable(original, r.flash);
  const payloadBytes = fromBase64(data.payload.base64);
  const regions = [...table.regions, { rt: r.payload_ram, fo: data.payload.offset, size: payloadBytes.length }];
  const anchors = findAnchors(original, regions, r.anchors);
  const { found, missing } = anchors;

  // The payload with this version's addresses in its import table (imports.S)
  const imp = data.payload.imports;
  if (u32(payloadBytes, imp) !== 0x31504D49 || u32(payloadBytes, imp + 4) !== r.imports.length) {
    throw new Error('The payload\'s import table does not match the manifest.');
  }
  r.imports.forEach((name, i) => payloadBytes.set(word(found[name] ?? 0), imp + 8 + 4 * i));

  // Padded like applyPatches pads it, so payload addresses can be checked too
  const padded = new Uint8Array(data.payload.offset + payloadBytes.length).fill(0xFF);
  padded.set(original, 0);
  padded.set(payloadBytes, data.payload.offset);

  const report = [], patches = [];
  for (const p of data.patches) {
    const problems = p.imports.filter(n => !(n in found)).map(n => `${n}: ${missing[n] || 'not found'}`);
    const changes = [];
    for (const t of p.relocatable) {
      try {
        const at = encode([{ word: t.at }], found, 0).reduce((v, b, i) => v + b * 256 ** i, 0);
        const off = toFile(regions, at);
        if (off === null) throw new Error(`${addrHex(at)} is not in the image`);
        const old = encode(t.old, found, at), now = encode(t.new, found, at);
        if (old.length !== now.length) throw new Error('old and new bytes differ in length');
        const have = padded.subarray(off, off + old.length);
        if (old.some((b, i) => b !== null && b !== have[i])) {
          throw new Error(`at ${addrHex(at)} the firmware has ${hex(have)}`);
        }
        changes.push({ offset: off, old: hex(have), new: hex(now), why: t.why });
      } catch (e) {
        problems.push(`${t.why}: ${e.message}`);
      }
    }
    report.push({ id: p.id, name: p.name, ok: !problems.length, problems });
    if (!problems.length) patches.push({ ...p, changes });
  }
  const plan = {
    firmware: [],
    payload: { offset: data.payload.offset, imports: imp, bytes: payloadBytes },
    payload_setup: payloadSetup(original, table, data, payloadBytes.length),
    patches,
  };
  return { plan, patches: report, anchors };
}
