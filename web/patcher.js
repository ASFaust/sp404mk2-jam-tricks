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
    const payload = fromBase64(data.payload.base64);
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
