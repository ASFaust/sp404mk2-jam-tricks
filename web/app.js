// The page: file input, patch list, build and download. The work is in patcher.js.
import { readFirmware, identify, resolve, selectionProblem, applyPatches } from './patcher.js';

const $ = id => document.getElementById(id);
let data = null;        // patches.json
let firmware = null;    // {bytes, name, version, plan} once a usable file is chosen: a known
                        // version with patches.json, or version null with what resolve() found

function setStatus(el, text, kind) {
  el.textContent = text;
  el.className = 'status' + (kind ? ' ' + kind : '');
}

function selected() {
  return [...document.querySelectorAll('#patches input:checked')].map(i => i.value);
}

// Only the patches this firmware can take can be chosen (null: all)
function offer(ids) {
  for (const input of document.querySelectorAll('#patches input')) {
    const ok = !ids || ids.includes(input.value);
    input.disabled = !ok;
    if (!ok) input.checked = false;
    input.closest('.patch').classList.toggle('disabled', !ok);
  }
}

function update() {
  const ids = selected();
  const problem = data && selectionProblem(data, ids);
  $('build').disabled = !data || !firmware || !!problem;
  setStatus($('buildStatus'), firmware && ids.length && problem ? problem : '');
}

function renderPatches() {
  const list = $('patches');
  for (const p of data.patches) {
    const li = document.createElement('li');
    li.innerHTML = `<label class="patch"><input type="checkbox"><div>
      <div class="name"></div><div class="text"></div></div></label>`;
    const input = li.querySelector('input');
    input.value = p.id;
    input.dataset.group = p.group || '';
    li.querySelector('.name').textContent = p.name;
    li.querySelector('.text').textContent = p.text;
    if (p.warning) {
      const w = document.createElement('div');
      w.className = 'warning';
      w.textContent = p.warning;
      li.querySelector('.text').after(w);
    }
    input.addEventListener('change', () => {
      if (input.checked && p.group) {   // one per group: the others in it switch off
        for (const other of list.querySelectorAll('input')) {
          if (other !== input && other.dataset.group === p.group) other.checked = false;
        }
      }
      update();
    });
    list.append(li);
  }
}

async function chooseFile(file) {
  firmware = null;
  offer(null);
  update();
  setStatus($('fileStatus'), `Reading ${file.name}…`);
  const tested = data.firmware.map(f => f.version).join(' or ');
  try {
    const fw = await readFirmware(new Uint8Array(await file.arrayBuffer()), file.name);
    const known = await identify(fw.bytes, data);
    if (known) {
      firmware = { ...fw, version: known.version, plan: data };
      setStatus($('fileStatus'), `✓ ${fw.name}: Roland’s version ${known.version}`, 'good');
      update();
      return;
    }
    // Another version (or a changed file): look for every place the patches need
    let found = null;
    try {
      found = resolve(fw.bytes, data);
    } catch (e) {
      console.log('resolve:', e);
    }
    const ok = found ? found.patches.filter(p => p.ok).map(p => p.id) : [];
    if (!ok.length) {
      setStatus($('fileStatus'), `${fw.name} isn’t Roland’s version ${tested}, and the patches ` +
        'can’t find their places in it. Is it Roland’s unchanged SP404MKII_APP1.bin?', 'bad');
      return;
    }
    console.log('patches not found in this firmware:', found.patches.filter(p => !p.ok));
    firmware = { ...fw, version: null, plan: found.plan };
    offer(ok);
    const missing = data.patches.length - ok.length;
    setStatus($('fileStatus'), `⚠ ${fw.name} is not version ${tested}, the one these patches are ` +
      `tested on. The page found the places for ${ok.length} of ${data.patches.length} patches` +
      (missing ? ' (the others are greyed out)' : '') + ', but nobody has tried the result on ' +
      `an SP-404MKII yet. If you can, update to ${tested} first.`);
  } catch (e) {
    setStatus($('fileStatus'), e.message, 'bad');
  }
  update();
}

function build() {
  const ids = selected();
  try {
    const out = applyPatches(firmware.bytes, firmware.plan, ids);
    const url = URL.createObjectURL(new Blob([out], { type: 'application/octet-stream' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'SP404MKII_APP1.bin';
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    const names = ids.map(id => data.patches.find(p => p.id === id).name);
    const which = firmware.version ? `version ${firmware.version}` : 'your (untested) version';
    setStatus($('buildStatus'), `✓ Built SP404MKII_APP1.bin for ${which} with: ` +
      names.join(', ') + '. Install it as described below.', 'good');
  } catch (e) {
    setStatus($('buildStatus'), e.message, 'bad');
  }
}

async function main() {
  try {
    const embedded = document.getElementById('patch-manifest');
    data = embedded ? JSON.parse(embedded.textContent) : await (await fetch('patches.json')).json();
  } catch (e) {
    setStatus($('fileStatus'), 'The patch list could not be loaded. Please reload the page.', 'bad');
    return;
  }
  renderPatches();
  $('footer').textContent = `Patches built ${data.built} (${data.source}) for firmware ` +
    data.firmware.map(f => f.version).join(', ') + '.';

  $('file').addEventListener('change', e => e.target.files[0] && chooseFile(e.target.files[0]));
  const drop = $('drop');
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => {
    e.preventDefault();
    drop.classList.remove('over');
    if (e.dataTransfer.files[0]) chooseFile(e.dataTransfer.files[0]);
  });
  $('build').addEventListener('click', build);
  update();
}

main();
