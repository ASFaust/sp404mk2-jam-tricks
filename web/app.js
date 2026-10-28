// The page: file input, patch list, build and download. The work is in patcher.js.
import { readFirmware, alreadyPatched, identify, resolve, selectionProblem, applyPatches } from './patcher.js';

const $ = id => document.getElementById(id);
let data = null;        // patches.json
let firmware = null;    // {bytes, name, version, plan} once a usable file is chosen: a known
                        // version with patches.json, or version null with what resolve() found

function setStatus(el, text, kind) {
  el.textContent = text;
  el.className = 'status' + (kind ? ' ' + kind : '');
}

// A card's patch, or its option (option_of in patches.json) instead when that is switched on
function selected() {
  const ids = [];
  for (const card of document.querySelectorAll('#patches .card')) {
    const main = card.querySelector('.patch input');
    const option = card.querySelector('.option input');
    if (main.checked) ids.push(option && option.checked && !option.disabled ? option.value : main.value);
  }
  return ids;
}

// Only the patches this firmware can take can be chosen (null: all)
function offer(ids) {
  for (const input of document.querySelectorAll('#patches input')) {
    const ok = !ids || ids.includes(input.value);
    input.dataset.offered = ok ? '1' : '';
    if (!ok) input.checked = false;
  }
  refreshCards();
}

function refreshCards() {
  for (const card of document.querySelectorAll('#patches .card')) {
    const main = card.querySelector('.patch input');
    main.disabled = !main.dataset.offered;
    card.classList.toggle('disabled', main.disabled);
    card.classList.toggle('on', main.checked);
    const option = card.querySelector('.option input');
    if (option) {
      option.disabled = !main.checked || !option.dataset.offered;
      option.closest('.option').classList.toggle('disabled', option.disabled);
    }
  }
}

function update() {
  refreshCards();
  const ids = selected();
  const problem = data && selectionProblem(data, ids);
  $('build').disabled = !data || !firmware || !!problem;
  setStatus($('buildStatus'), firmware && ids.length && problem ? problem : '');
}

function describe(el, p) {
  el.querySelector('.name').textContent = p.name;
  el.querySelector('.text').textContent = p.summary;
}

// The full text of a patch and its options, with their warnings
function showInfo(p) {
  $('infoTitle').textContent = p.name;
  const body = $('infoBody');
  body.replaceChildren();
  const add = (cls, text) => {
    const el = document.createElement('p');
    el.className = cls;
    el.textContent = text;
    body.append(el);
  };
  add('', p.text);
  if (p.warning) add('warning', p.warning);
  for (const o of data.patches.filter(o => o.option_of === p.id)) {
    add('option-head', o.name);
    add('', o.text);
    if (o.warning) add('warning', o.warning);
  }
  $('infoPopup').showModal();
}

function renderPatches() {
  const list = $('patches');
  for (const p of data.patches.filter(p => !p.option_of)) {
    const li = document.createElement('li');
    li.className = 'card';
    li.innerHTML = `<label class="patch"><input type="checkbox" class="sr"><span class="tick"></span>
      <div><div class="name"></div><div class="text"></div></div></label>`;
    const input = li.querySelector('input');
    input.value = p.id;
    input.dataset.group = p.group || '';
    describe(li.querySelector('.patch'), p);
    const info = document.createElement('button');
    info.className = 'info-btn';
    info.type = 'button';
    info.textContent = 'i';
    info.setAttribute('aria-label', `More about ${p.name}`);
    info.addEventListener('click', () => showInfo(p));
    li.append(info);
    const options = data.patches.filter(o => o.option_of === p.id);
    if ([p, ...options].some(q => q.warning)) {
      const note = document.createElement('button');
      note.className = 'note';
      note.type = 'button';
      note.textContent = 'Affects saved projects';
      note.addEventListener('click', () => showInfo(p));
      li.querySelector('.patch .text').after(note);
    }
    for (const o of options) {
      const label = document.createElement('label');
      label.className = 'option';
      label.innerHTML = `<input type="checkbox" class="sr"><span class="switch"></span>
        <div><span class="name"></span> <span class="text"></span></div>`;
      label.querySelector('input').value = o.id;
      describe(label, o);
      label.querySelector('input').addEventListener('change', update);
      li.append(label);
    }
    input.addEventListener('change', () => {
      if (input.checked && p.group) {   // one per group: the others in it switch off
        for (const other of list.querySelectorAll('.patch input')) {
          if (other !== input && other.dataset.group === p.group) other.checked = false;
        }
      }
      update();
    });
    list.append(li);
  }
  offer(null);
}

async function chooseFile(file) {
  firmware = null;
  offer(null);
  update();
  setStatus($('fileStatus'), `Reading ${file.name}…`);
  const tested = data.firmware.map(f => f.version).join(' or ');
  try {
    const fw = await readFirmware(new Uint8Array(await file.arrayBuffer()), file.name);
    const patched = alreadyPatched(fw.bytes, data);
    if (patched) {
      setStatus($('fileStatus'), `${fw.name} was already patched with ${patched}. Choose Roland’s ` +
        'original zip instead: the page always starts from Roland’s unchanged firmware.', 'bad');
      return;
    }
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
    const cards = data.patches.filter(p => !p.option_of);
    const foundCards = cards.filter(p => ok.includes(p.id)).length;
    const missing = cards.length - foundCards;
    setStatus($('fileStatus'), `⚠ ${fw.name} is not version ${tested}, the one these patches are ` +
      `tested on. The page found the places for ${foundCards} of ${cards.length} patches` +
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
    const names = ids.map(id => {
      const p = data.patches.find(q => q.id === id);
      const main = p.option_of && data.patches.find(q => q.id === p.option_of);
      return main ? `${main.name} (${p.name.toLowerCase()})` : p.name;
    });
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
  $('titleVersion').textContent = `v${data.version}`;   // the HTML's own is a fallback
  document.title = `SP-404MKII Jam Tricks v${data.version}`;
  $('footer').textContent = `Jam Tricks v${data.version}, built ${data.built} (${data.source}) for firmware ` +
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
  $('infoClose').addEventListener('click', () => $('infoPopup').close());
  $('infoPopup').addEventListener('click', e => {   // a click on the backdrop closes it
    if (e.target === $('infoPopup')) $('infoPopup').close();
  });
  update();
}

main();
