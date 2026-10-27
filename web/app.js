// The page: file input, patch list, build and download. The work is in patcher.js.
import { readFirmware, identify, selectionProblem, applyPatches } from './patcher.js';

const $ = id => document.getElementById(id);
let data = null;        // patches.json
let firmware = null;    // {bytes, name, version} once a supported file is chosen

function setStatus(el, text, kind) {
  el.textContent = text;
  el.className = 'status' + (kind ? ' ' + kind : '');
}

function selected() {
  return [...document.querySelectorAll('#patches input:checked')].map(i => i.value);
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
  update();
  setStatus($('fileStatus'), `Reading ${file.name}…`);
  try {
    const fw = await readFirmware(new Uint8Array(await file.arrayBuffer()), file.name);
    const known = await identify(fw.bytes, data);
    if (!known) {
      setStatus($('fileStatus'), `${fw.name} isn’t Roland’s version ` +
        data.firmware.map(f => f.version).join(' or ') + ' (or it was changed). For now the ' +
        'patcher works with that version only.', 'bad');
      return;
    }
    firmware = { ...fw, version: known.version };
    setStatus($('fileStatus'), `✓ ${fw.name}: Roland’s version ${known.version}`, 'good');
  } catch (e) {
    setStatus($('fileStatus'), e.message, 'bad');
  }
  update();
}

function build() {
  const ids = selected();
  try {
    const out = applyPatches(firmware.bytes, data, ids);
    const url = URL.createObjectURL(new Blob([out], { type: 'application/octet-stream' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'SP404MKII_APP1.bin';
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    const names = ids.map(id => data.patches.find(p => p.id === id).name);
    setStatus($('buildStatus'), `✓ Built SP404MKII_APP1.bin for version ${firmware.version} with: ` +
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
