// Pure UI-state tests. No server, device, audio or network access.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Node {
  constructor(tag = '') { this.tag = tag; this.children = []; this.value = ''; this.checked = false; this.disabled = false; this.attributes = {}; this.dataset = {}; this.listeners = {}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(key, fn) { this.listeners[key] = fn; }
  reportValidity() { return true; }
  get firstChild() { return this.children[0]; }
}
function harness(fetchMock) {
  const nodes = new Map();
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  const source = fs.readFileSync(path.join(__dirname, '../static/speech-verification.js'), 'utf8');
  const marker = '  initialize();';
  assert.equal(source.split(marker).length, 2);
  const context = {document: {getElementById: get, querySelector: () => ({content: 'test-token'}), createElement: tag => new Node(tag)},
    Option: function(label, value) { const node = new Node('option'); node.textContent = label; node.value = value; return node; },
    window: {addEventListener() {}}, FormData,
    setTimeout, clearTimeout, AbortController, fetch: fetchMock || (() => { throw new Error('Network forbidden'); })};
  vm.createContext(context);
  vm.runInContext(source.replace(marker, 'globalThis.ui = {state, pairFor, connectNative, nativeImportItems, importNative, renderCatalog, localIssues, previewReady, previewBody, appendResultStatus, attemptDetails, sync, action, resetDevice, resetSubject, loadSubjects, loadCatalog, loadAssets, createPreview, bindEvents, renderSubjects, updateDevices, refreshStatus};'), context);
  context.ui.state.connected = true;
  context.ui.state.device = 'device-a';
  context.ui.state.subject = 'japanese-premium';
  context.ui.state.subjects = [supportedSubject];
  context.ui.state.subjectsDevice = 'device-a';
  context.ui.state.subjectsStatus = 'ready';
  context.ui.state.catalogSubject = 'japanese-premium';
  get('device').value = 'device-a';
  get('subject').value = 'japanese-premium';
  return {...context.ui, get};
}
const supportedSubject = {id:'japanese-premium',name:'Japanese Premium',package:'test.premium',build:11,supported:true,
  support_status:'supported',reason:'Runtime foreground checked later',capabilities:{catalog:true,native:true,input:true,verdict:true}};
const item = {word_id: '30056', text: 'test sentence', native_available: true};
test('input recording import is a visible separate action, not a native import', () => {
  const html = fs.readFileSync(path.join(__dirname, '../static/speech-verification.html'), 'utf8');
  assert.equal((html.match(/id="import-recordings"/g) || []).length, 1);
  assert.ok(html.indexOf('id="import-recordings"') < html.indexOf('id="asset-word-id"'));
  assert.ok(html.includes('음성인식 입력 기록 가져오기'));
  const h = harness(); h.sync();
  assert.equal(h.get('import-recordings').textContent, '음성인식 입력 기록 가져오기');
  assert.equal(h.get('import-recordings').disabled, false);
  assert.equal(h.get('start-run').disabled, true);
});
function walk(node) { return [node, ...node.children.flatMap(walk)]; }
test('device and explicit supported subject enable DB lookup, disconnect disables it', () => {
  const h = harness(); h.get('device').value = ''; h.sync();
  assert.equal(h.get('load-catalog').disabled, true);
  h.get('device').value = 'device-a'; h.sync(); assert.equal(h.get('load-catalog').disabled, false);
  h.state.subject = ''; h.get('subject').value = ''; h.sync(); assert.equal(h.get('load-catalog').disabled, true);
  h.state.subject = 'japanese-premium'; h.get('subject').value = 'japanese-premium';
  h.state.connected = false; h.sync(); assert.equal(h.get('load-catalog').disabled, true);
});
test('new rows default to Perfect and approved without approving execution', () => {
  const h = harness(), pair = h.pairFor(item);
  assert.equal(pair.approved, true); assert.equal(pair.expected_grades.join(','), 'Perfect');
  assert.equal(pair.asset_id, ''); h.sync();
  assert.equal(h.get('run-approved').checked, false); assert.equal(h.get('start-run').disabled, true);
});
test('user policy edits survive rerender and native audio connection', () => {
  const h = harness(); h.state.catalog = [item];
  const p = h.pairFor(item); p.approved = false; p.expected_grades = ['Good'];
  const asset = {id: 'native-a', kind: 'native', word_id: '30056'}; h.state.assets = [asset];
  h.connectNative(item, asset); h.renderCatalog();
  assert.equal(p.asset_id, asset.id); assert.equal(p.approved, false); assert.equal(p.expected_grades.join(','), 'Good');
});
test('native import connects exactly the returned WordID without clearing default approval', () => {
  const h = harness(); h.state.catalog = [item];
  const asset = {id: 'native-a', kind: 'native', word_id: '30056'}; h.state.assets = [asset];
  h.connectNative(item, asset);
  assert.equal(h.pairFor(item).asset_id, 'native-a'); assert.equal(h.pairFor(item).approved, true);
  assert.equal(h.get('run-approved').checked, false);
});
test('wrong/missing/native-kind-mismatch responses never connect', () => {
  for (const asset of [{id:'a',kind:'native',word_id:'999'}, {id:'a',kind:'human-recording',word_id:'30056'}]) {
    const h = harness(); h.state.assets = [asset];
    assert.throws(() => h.connectNative(item, asset)); assert.equal(h.pairFor(item).asset_id, '');
  }
  assert.throws(() => harness().connectNative(item, {id:'missing'}));
});
test('Perfect default does not bypass missing audio or preview checks', () => {
  const h = harness(); h.state.catalog = [item]; h.state.catalogDevice = 'device-a'; h.state.assetsLoaded = true; h.state.selected.add('30056');
  assert.ok(h.localIssues().some(x => x.includes('입력 음원 없음')));
  assert.equal(h.previewReady(), false);
});
test('recording import shows spinner and busy labels until success, preventing duplicate work', async () => {
  const h = harness(); let release, calls = 0;
  const work = h.action('입력 기록 가져오기', () => new Promise(resolve => {release = resolve;}));
  assert.equal(h.get('asset-loading').hidden, false);
  assert.equal(h.get('prepare-loading-text').textContent, '음성인식 입력 기록 불러오는 중…');
  assert.equal(h.get('import-recordings').textContent, '불러오는 중…');
  assert.equal(h.get('prepare-fields').attributes['aria-busy'], 'true');
  await h.action('입력 기록 가져오기', () => calls++); assert.equal(calls, 0);
  release(); await work;
  assert.equal(h.get('asset-loading').hidden, true); assert.equal(h.get('prepare-fields').disabled, false);
  assert.equal(h.get('import-recordings').textContent, '음성인식 입력 기록 가져오기');
});
test('loading failure restores controls and preserves error notice', async () => {
  const h = harness();
  await h.action('원본 음원 가져오기', async () => { throw new Error('expected failure'); });
  assert.equal(h.get('notice').textContent, 'expected failure');
  assert.equal(h.get('prepare-loading').hidden, true); assert.equal(h.get('prepare-fields').disabled, false);
});
test('unavailable native audio remains disabled and explicitly unconnected', () => {
  const h = harness(); h.state.catalog = [{...item,native_available:false}]; h.renderCatalog();
  const nodes = walk(h.get('catalog-rows'));
  assert.ok(nodes.some(n => n.tag === 'option' && n.textContent === '원본 음원 가져오기 · 미연결'));
  assert.ok(nodes.some(n => n.tag === 'button' && n.textContent === '원본 음원 가져오기' && n.disabled));
});
test('CSS has wider enabled-green lookup and accessible reduced-motion loader', () => {
  const html = fs.readFileSync(path.join(__dirname,'../static/speech-verification.html'),'utf8');
  assert.match(html, /#load-catalog\{min-width:164px;padding-inline:26px\}/);
  assert.match(html, /#load-catalog:not\(:disabled\)\{background:#246c57/);
  assert.match(html, /prefers-reduced-motion:reduce/);
  assert.match(html, /id="prepare-loading"[^>]+role="status"/);
});

test('input audio commands precede WordID and retain a separate recording control', () => {
  const html = fs.readFileSync(path.join(__dirname, '../static/speech-verification.html'), 'utf8');
  const ids = ['import-native', 'generate-tts', 'upload-asset', 'asset-word-id'];
  const positions = ids.map(id => html.indexOf('id="' + id + '"'));
  assert.ok(positions.every((position, index) => position >= 0 && (!index || position > positions[index - 1])));
  assert.match(html, /id="import-native"[^>]*>기기 음원 가져오기/);
  assert.match(html, /id="import-recordings"[^>]*>음성인식 입력 기록 가져오기/);
});

test('native scope is exact WordID or explicit selection, never implicit unit bulk', () => {
  const h = harness(); h.state.catalogDevice = 'device-a';
  h.state.catalog = [item, {...item, word_id:'30057', level:'2'}];
  h.get('mode-unit').checked = true;
  assert.equal(h.nativeImportItems().length, 0);
  h.state.selected.add('30057');
  assert.equal(h.nativeImportItems()[0].word_id, '30057');
  h.get('asset-word-id').value = '30056';
  assert.equal(h.nativeImportItems()[0].word_id, '30056');
  h.get('asset-word-id').value = '3005'; assert.equal(h.nativeImportItems().length, 0);
  h.get('asset-word-id').value = '30057'; h.get('level').value = '1';
  assert.equal(h.nativeImportItems().length, 0);
  h.get('level').value = ''; h.get('device').value = 'device-b';
  assert.equal(h.nativeImportItems().length, 0);
});

test('global native import calls only native API and connects the exact asset preserving defaults', async () => {
  const calls = [], asset = {id:'native-a',kind:'native',word_id:'30056'};
  const h = harness(async (url, options) => {
    calls.push({url, options});
    return {ok:true,status:200,json:async () => url.endsWith('/native') ? asset : {items:[asset]}};
  });
  h.state.catalogDevice = 'device-a'; h.state.catalog = [item];
  h.get('asset-word-id').value = '30056';
  await h.importNative();
  assert.equal(calls.length, 2);
  assert.ok(calls[0].url.endsWith('/native'));
  assert.deepEqual(JSON.parse(calls[0].options.body), {device:'device-a',subject:'japanese-premium',word_id:'30056'});
  assert.ok(calls[1].url.endsWith('/assets'));
  assert.equal(h.pairFor(item).asset_id, 'native-a');
  assert.equal(h.pairFor(item).approved, true);
  assert.equal(h.pairFor(item).expected_grades.join(','), 'Perfect');
  assert.equal(h.get('run-approved').checked, false);
});

test('missing or unavailable native targets perform no request', async () => {
  const h = harness(() => assert.fail('Unexpected request'));
  h.state.catalogDevice = 'device-a'; h.state.catalog = [{...item,native_available:false}];
  await h.importNative();
  h.get('asset-word-id').value = '30056'; await h.importNative();
  assert.equal(h.pairFor(item).asset_id, '');
});

test('native loading prevents duplicate imports and restores controls on request failure', async () => {
  let rejectRequest, calls = 0;
  const h = harness(() => { calls++; return new Promise((resolve, reject) => { rejectRequest = reject; }); });
  h.state.catalogDevice = 'device-a'; h.state.catalog = [item]; h.state.selected.add('30056');
  const work = h.importNative();
  assert.equal(h.get('import-native').textContent, '불러오는 중…');
  assert.equal(h.get('prepare-loading-text').textContent, '앱 원본 음원 불러오는 중…');
  assert.equal(h.get('import-native').attributes['aria-busy'], 'true');
  await h.importNative(); assert.equal(calls, 1);
  rejectRequest(new Error('native unavailable')); await work;
  assert.equal(h.get('import-native').textContent, '기기 음원 가져오기');
  assert.equal(h.get('prepare-fields').disabled, false);
  assert.equal(h.get('notice').textContent, 'native unavailable');
});

test('native import failure clears loading and never connects a recording asset', async () => {
  const asset = {id:'wrong',kind:'human-recording',word_id:'30056'};
  const h = harness(async url => ({ok:true,status:200,json:async () => url.endsWith('/native') ? asset : {items:[asset]}}));
  h.state.catalogDevice = 'device-a'; h.state.catalog = [item]; h.state.selected.add('30056');
  await h.importNative();
  assert.equal(h.pairFor(item).asset_id, '');
  assert.equal(h.get('import-native').attributes['aria-busy'], 'false');
  assert.equal(h.get('prepare-fields').disabled, false);
  assert.match(h.get('notice').textContent, /연결을 확인할 수 없습니다/);
});

const wordItem = {...item, kind:'word'};
function wordHarness() {
  const h = harness();
  h.state.catalog = [wordItem]; h.state.catalogDevice = 'device-a';
  h.state.selected.add(wordItem.word_id); h.state.assetsLoaded = true;
  const asset = {id:'word-native',kind:'native',word_id:wordItem.word_id};
  h.state.assets = [asset]; h.connectNative(wordItem, asset);
  return h;
}
function approvalInput(h) {
  return walk(h.get('catalog-rows')).find(n => n.tag === 'label' && n.children.some(child => child.textContent === '승인')).children[0];
}

test('word defaults require explicit approval with outcomes separate from sentence grades', () => {
  const h = wordHarness(), pair = h.pairFor(wordItem);
  assert.equal(pair.approved, false);
  assert.equal(pair.expected_outcomes.length, 0);
  assert.equal(pair.expected_grades.length, 0);
  assert.equal(pair.expect_reject, false);
  assert.ok(h.localIssues().some(issue => issue.includes('단어 기대 결과 승인 필요')));
  const nodes = walk(h.get('catalog-rows'));
  assert.equal(nodes.filter(n => n.tag === 'input' && n.type === 'radio').length, 2);
  assert.ok(nodes.filter(n => n.tag === 'input' && n.type === 'radio').every(n => !n.checked));
  assert.ok(!nodes.some(n => n.textContent === 'Perfect' || n.textContent === '인식 거부 기대'));
  assert.equal(approvalInput(h).disabled, true);
  const correct = nodes.find(n => n.tag === 'input' && n.value === 'word_correct');
  correct.checked = true; correct.listeners.change();
  assert.equal(pair.approved, false);
  const approval = approvalInput(h); approval.checked = true; approval.listeners.change();
  assert.equal(pair.approved, true);
  assert.equal(h.localIssues().length, 0);
  const body = h.previewBody().items[0];
  assert.equal(body.expected_outcomes.join(','), 'word_correct');
  assert.equal(Object.hasOwn(body, 'expected_grades'), false);
  assert.equal(Object.hasOwn(body, 'expect_reject'), false);
  assert.equal(h.get('run-approved').checked, false);
});

test('changing word outcome clears prior approval and invalidates preview', () => {
  const h = wordHarness(), pair = h.pairFor(wordItem);
  pair.approved = true; h.state.preview = {ready:true}; h.get('run-approved').checked = true;
  const wrong = walk(h.get('catalog-rows')).find(n => n.tag === 'input' && n.value === 'word_wrong');
  wrong.checked = true; wrong.listeners.change();
  assert.equal(pair.expected_outcomes.join(','), 'word_wrong');
  assert.equal(pair.approved, false);
  assert.equal(h.state.preview, null);
  assert.equal(h.get('run-approved').checked, false);
  assert.equal(approvalInput(h).checked, false);
  assert.equal(h.previewBody().items[0].expected_outcomes.join(','), 'word_wrong');
});

test('existing approved Perfect word policy remains unchanged and unverified until explicit edit', () => {
  const h = harness(); h.state.catalog = [wordItem]; h.state.selected.add(wordItem.word_id);
  const legacy = {asset_id:'',approved:true,expected_grades:['Perfect'],expect_reject:false};
  h.state.pairs.set(wordItem.word_id, legacy);
  const before = JSON.stringify(legacy);
  h.renderCatalog();
  assert.equal(JSON.stringify(h.pairFor(wordItem)), before);
  assert.equal(approvalInput(h).disabled, true);
  assert.ok(h.localIssues().some(issue => issue.includes('단어 정답/오답 기준 미검증')));
  const body = h.previewBody().items[0];
  assert.equal(body.approved, true); assert.equal(body.expected_grades.join(','), 'Perfect');
  assert.equal(Object.hasOwn(body, 'expected_outcomes'), false);
  const correct = walk(h.get('catalog-rows')).find(n => n.tag === 'input' && n.value === 'word_correct');
  assert.equal(correct.checked, false);
  correct.checked = true; correct.listeners.change();
  assert.equal(legacy.approved, false);
  assert.equal(legacy.expected_grades.length, 0);
  assert.equal(legacy.expected_outcomes.join(','), 'word_correct');
});

test('sentence preview keeps grade policy and default approval', () => {
  const h = harness(); h.state.catalog = [item]; h.state.selected.add(item.word_id);
  const body = h.previewBody().items[0];
  assert.equal(body.approved, true);
  assert.equal(body.expected_grades.join(','), 'Perfect');
  assert.equal(body.expect_reject, false);
  assert.equal(Object.hasOwn(body, 'expected_outcomes'), false);
});

test('result views show native word outcome separately without deriving grade or mutating history', () => {
  const h = harness(), target = new Node();
  const result = {kind:'word',status:'unverified',policy:{approved:true,expected_grades:['Perfect'],expect_reject:false},
    attempts:[{app_outcome:'word_correct',app_grade:null,app_score:100,
      word_phases:[{word_phase:1,app_outcome:'word_correct',full_input:true},
                   {word_phase:2,app_outcome:null,full_input:false,reason:'missing'}]}]};
  const before = JSON.stringify(result);
  h.appendResultStatus(target, result);
  const text = walk(target).map(n => n.textContent).filter(Boolean).join(' ');
  assert.match(text, /앱 단어 결과 · 정답/);
  assert.match(text, /기대 등급 · Perfect/);
  assert.doesNotMatch(text, /앱 등급 · Perfect/);
  const details = h.attemptDetails({id:'unchanged-history'}, result);
  assert.ok(walk(details).some(n => n.textContent === '앱 단어 결과'));
  assert.ok(walk(details).some(n => n.textContent === '정답'));
  assert.ok(walk(details).some(n => n.textContent === '단어 입력 1단계 · 정답'));
  assert.ok(walk(details).some(n => n.textContent === '전체 음성 전달 · 미확인'));
  assert.equal(JSON.stringify(result), before);
  const newPolicy = new Node();
  h.appendResultStatus(newPolicy, {...result,policy:{approved:true,expected_outcomes:['word_wrong']}});
  assert.ok(walk(newPolicy).some(n => n.textContent === '기대 단어 결과 · 오답'));
});

function response(data) { return {ok:true,status:200,json:async () => data}; }
const flush = () => new Promise(resolve => setImmediate(resolve));
function queuedHarness({deferAssets = false} = {}) {
  const calls = [], pending = [];
  const h = harness((url, options) => {
    const call = {path:url.replace('/api/speech-verification', ''),method:options.method,
      body:options.body instanceof FormData ? options.body : options.body ? JSON.parse(options.body) : undefined};
    calls.push(call);
    if (call.path === '/prepare/reset') return Promise.resolve(response({reset:true}));
    if (call.path === '/assets' && call.method === 'GET' && !deferAssets) return Promise.resolve(response({items:[]}));
    return new Promise((resolve, reject) => pending.push({...call,resolve:data => resolve(response(data)),reject}));
  });
  h.bindEvents();
  const take = path => {
    const index = pending.findIndex(call => call.path === path);
    assert.notEqual(index, -1, 'Missing pending ' + path);
    return pending.splice(index, 1)[0];
  };
  return {...h, calls, pending, take};
}
function chooseSubject(h, id) { h.get('subject').value = id; h.resetSubject(); }
function prepareCatalog(h) { h.state.catalog = [item]; h.state.catalogDevice = h.state.device; h.state.catalogSubject = h.state.subject; h.state.selected.add(item.word_id); }

test('environment orders device, installed registered subject, then DB without a default subject', () => {
  const html = fs.readFileSync(path.join(__dirname, '../static/speech-verification.html'), 'utf8');
  const positions = ['device', 'subject', 'load-catalog'].map(id => html.indexOf('id="' + id + '"'));
  assert.ok(positions.every((position, index) => !index || position > positions[index - 1]));
  assert.match(html, /id="subject"[^>]*disabled><option value="">과목 선택/);
  assert.ok(html.indexOf('id="prepare-fields"') > positions[2]);
  assert.match(html, /aria-describedby="subject-state"/);
});

test('inventory loading, empty, error and explicit selection states gate DB lookup', async () => {
  const h = queuedHarness();
  chooseSubject(h, '');
  let work = h.loadSubjects();
  assert.equal(h.get('subject').disabled, true);
  assert.match(h.get('subject-state').textContent, /설치된 등록 과목 확인 중/);
  const request = h.take('/subjects');
  assert.equal(request.method, 'POST'); assert.deepEqual(request.body, {device:'device-a'});
  request.resolve({device:'device-a',items:[]}); await work;
  assert.match(h.get('subject-state').textContent, /설치된 등록 과목 없음/);
  assert.equal(h.get('load-catalog').disabled, true);
  work = h.loadSubjects(); h.take('/subjects').reject(new Error('inventory failed')); await work;
  assert.match(h.get('subject-state').textContent, /확인 실패.*inventory failed/);
  work = h.loadSubjects(); h.take('/subjects').resolve({device:'device-a',items:[supportedSubject]}); await work;
  assert.equal(h.state.subject, ''); assert.equal(h.get('subject').value, '');
  assert.equal(h.get('subject').disabled, false); assert.equal(h.get('load-catalog').disabled, true);
  chooseSubject(h, supportedSubject.id);
  assert.equal(h.get('load-catalog').disabled, false);
  assert.match(h.get('subject-state').textContent, /Runtime foreground checked later/);
  await flush();
});

test('unsupported installed subjects stay selectable with reasons but never issue preparation mutations', async () => {
  const h = queuedHarness();
  h.state.subjects = ['unsupported_build','not_integrated','unavailable'].map((status, index) =>
    ({...supportedSubject,id:'unsupported-' + index,supported:false,support_status:status,reason:'reason-' + status}));
  h.renderSubjects();
  for (const subject of h.state.subjects) {
    assert.equal(h.get('subject').children.find(node => node.value === subject.id).disabled, false);
    chooseSubject(h, subject.id);
    assert.match(h.get('subject-state').textContent, new RegExp(subject.reason));
    for (const id of ['load-catalog','import-native','import-recordings','preview-button','upload-asset','generate-tts']) assert.equal(h.get(id).disabled, true, id);
    await h.loadCatalog(); await h.importNative(); await h.createPreview();
    await h.get('import-recordings').listeners.click(); await h.get('generate-tts').listeners.click();
    await h.get('asset-form').listeners.submit({preventDefault() {}});
  }
  await flush();
  assert.ok(h.calls.every(call => call.path === '/assets' && call.method === 'GET'));
});

test('capabilities gate each supported-subject operation independently', () => {
  const h = harness(); prepareCatalog(h);
  h.state.subjects = [{...supportedSubject,capabilities:{catalog:true,native:false,input:false,verdict:false}}];
  h.sync();
  assert.equal(h.get('load-catalog').disabled, false);
  for (const id of ['import-native','import-recordings','preview-button','upload-asset','generate-tts']) assert.equal(h.get(id).disabled, true, id);
});

for (const returnToA of [false, true]) {
  test('pending subject inventory ignores stale device A->B' + (returnToA ? '->A' : ''), async () => {
    const h = queuedHarness();
    chooseSubject(h, '');
    const oldWork = h.loadSubjects(), old = h.take('/subjects');
    h.get('device').value = 'device-b';
    const bWork = h.resetDevice(), b = h.take('/subjects');
    let currentWork = bWork, current = b;
    if (returnToA) {
      h.get('device').value = 'device-a';
      currentWork = h.resetDevice(); current = h.take('/subjects');
    }
    current.resolve({device:h.state.device,items:[supportedSubject]}); await currentWork;
    chooseSubject(h, supportedSubject.id);
    old.resolve({device:'device-a',items:[{...supportedSubject,id:'stale-app'}]}); await oldWork;
    if (returnToA) { b.reject(new Error('stale B error')); await bWork; }
    assert.equal(h.state.subjects.length, 1); assert.equal(h.state.subjects[0].id, supportedSubject.id);
    assert.equal(h.state.subject, supportedSubject.id); assert.equal(h.get('subject').value, supportedSubject.id);
    assert.equal(h.state.subjectsDevice, h.state.device);
    assert.doesNotMatch(h.get('subject-state').textContent, /stale/);
    await flush(); assert.equal(h.pending.length, 0);
  });
}

for (const axis of ['device', 'subject']) {
  for (const returnToA of [false, true]) {
    test('pending catalog ignores stale ' + axis + ' A->B' + (returnToA ? '->A' : '') + ' without unlocking current request', async () => {
      const h = queuedHarness();
      const second = {...supportedSubject,id:'test-second'};
      h.state.subjects.push(second);
      const oldWork = h.loadCatalog(), old = h.take('/catalog');
      async function switchContext(value) {
        if (axis === 'device') {
          h.get('device').value = value;
          const work = h.resetDevice();
          h.take('/subjects').resolve({device:value,items:[supportedSubject,second]}); await work;
          chooseSubject(h, supportedSubject.id);
        } else chooseSubject(h, value);
        await flush();
      }
      await switchContext(axis === 'device' ? 'device-b' : second.id);
      const bWork = h.loadCatalog(), b = h.take('/catalog');
      let currentWork = bWork, current = b;
      if (returnToA) {
        await switchContext(axis === 'device' ? 'device-a' : supportedSubject.id);
        currentWork = h.loadCatalog(); current = h.take('/catalog');
      }
      const pendingNotice = h.get('notice').textContent;
      old.resolve({device:old.body.device,subject:old.body.subject,items:[{...item,text:'stale'}]}); await oldWork;
      if (returnToA) { b.reject(new Error('stale failure')); await bWork; }
      assert.equal(h.state.catalog.length, 0);
      assert.equal(h.state.pending, 'DB 목록 조회');
      assert.equal(h.get('notice').textContent, pendingNotice);
      assert.equal(h.get('prepare-fields').disabled, true);
      assert.equal(h.get('device').disabled, false);
      assert.equal(h.get('subject').disabled, false);
      current.resolve({device:current.body.device,subject:current.body.subject,items:[item]}); await currentWork;
      assert.equal(h.state.catalog[0].text, item.text);
      assert.equal(h.state.catalogDevice, h.state.device); assert.equal(h.state.catalogSubject, h.state.subject);
      assert.equal(h.state.pending, ''); assert.equal(h.pending.length, 0);
    });
  }
}

for (const axis of ['device', 'subject']) {
  test(axis + ' change clears preparation only, retains library/history/evidence, and resets only its preview', async () => {
    const h = queuedHarness(); prepareCatalog(h); h.pairFor(item);
    const retained = {active:{id:'active',status:'finished',results:[{word_id:1}]},inspected:{id:'history'},comparison:{id:'comparison'},
      history:[{id:'history'}],assets:[{id:'keep-audio'}],downloadUrls:new Set(['blob:evidence'])};
    Object.assign(h.state, retained); h.state.assetsLoaded = true;
    h.state.evidenceGeneration = 9; h.state.resultSignature = 'retained'; h.state.resultPage = 4;
    h.state.preview = {id:'preview-to-clear'}; h.state.previewRequest = {device:'device-a'}; h.state.previewRevision = 8;
    for (const id of ['level','week','stage','catalog-search','asset-word-id','asset-text','asset-file']) h.get(id).value = 'old';
    h.get('run-approved').checked = true; h.get('mode-unit').checked = true;
    h.get('import-report').hidden = false; h.get('import-issues').append(new Node());
    if (axis === 'device') {
      h.get('device').value = 'device-b';
      const work = h.resetDevice();
      assert.equal(h.state.subject, ''); assert.equal(h.state.subjects.length, 0);
      h.take('/subjects').resolve({device:'device-b',items:[]}); await work;
    } else chooseSubject(h, '');
    // The read-only refresh may update the library; the synchronous reset must never delete it.
    if (axis === 'subject') assert.strictEqual(h.state.assets, retained.assets);
    for (const key of ['active','inspected','comparison','history','downloadUrls']) assert.strictEqual(h.state[key], retained[key]);
    assert.equal(h.state.evidenceGeneration, 9); assert.equal(h.state.resultSignature, 'retained'); assert.equal(h.state.resultPage, 4);
    assert.equal(h.state.catalog.length, 0); assert.equal(h.state.selected.size, 0); assert.equal(h.state.pairs.size, 0);
    assert.equal(h.state.preview, null); assert.equal(h.state.previewRequest, null); assert.equal(h.state.previewRevision, -1);
    for (const id of ['level','week','stage','catalog-search','asset-word-id','asset-text','asset-file']) assert.equal(h.get(id).value, '', id);
    assert.equal(h.get('run-approved').checked, false); assert.equal(h.get('mode-unit').checked, false);
    assert.equal(h.get('import-report').hidden, true); assert.equal(h.get('import-issues').children.length, 0);
    assert.deepEqual(h.calls.filter(call => call.path === '/prepare/reset').map(call => call.body), [{preview_id:'preview-to-clear'}]);
    await flush();
  });
}

test('stale preview returned after subject A->B->A is discarded and reset, never approved', async () => {
  const h = queuedHarness(); prepareCatalog(h);
  const work = h.createPreview(), old = h.take('/preview');
  assert.equal(old.body.subject, supportedSubject.id);
  chooseSubject(h, ''); chooseSubject(h, supportedSubject.id); await flush();
  old.resolve({id:'late-preview',ready:true,device:'device-a',subject:supportedSubject.id,items:[item]}); await work;
  assert.equal(h.state.preview, null); assert.equal(h.get('run-approved').checked, false);
  assert.equal(h.get('start-run').disabled, true);
  assert.deepEqual(h.calls.filter(call => call.path === '/prepare/reset').map(call => call.body), [{preview_id:'late-preview'}]);
});

test('stale native response cannot restore pairings or continue a bulk import', async () => {
  const h = queuedHarness(); prepareCatalog(h);
  h.state.catalog.push({...item,word_id:'30057'}); h.state.selected.add('30057');
  const work = h.importNative(), old = h.take('/native');
  chooseSubject(h, ''); chooseSubject(h, supportedSubject.id); await flush();
  old.resolve({id:'late-audio',kind:'native',word_id:item.word_id,subject:supportedSubject.id}); await work;
  assert.equal(h.state.pairs.size, 0); assert.equal(h.state.catalog.length, 0);
  assert.equal(h.calls.filter(call => call.path === '/native').length, 1);
  assert.equal(h.state.assets.length, 0);
});

test('stale recordings import cannot restore report or asset list', async () => {
  const h = queuedHarness();
  const work = h.get('import-recordings').listeners.click(), old = h.take('/recordings/import');
  assert.deepEqual(old.body, {device:'device-a',subject:supportedSubject.id,limit:20,after:''});
  chooseSubject(h, ''); await flush();
  const callsBefore = h.calls.length;
  old.resolve({assets:[{id:'stale'}],errors:[{filename:'stale',reason:'stale'}]}); await work;
  assert.equal(h.get('import-report').hidden, true); assert.equal(h.get('import-issues').children.length, 0);
  assert.equal(h.calls.length, callsBefore); assert.equal(h.state.assets.length, 0);
});

test('recording import advances bounded batches and reports progress before loading assets', async () => {
  const h = queuedHarness();
  const work = h.get('import-recordings').listeners.click();
  h.take('/recordings/import').resolve({assets:[{id:'a'}],errors:[],skipped:[],processed:20,total:21,next_after:'20000101_120000_020.txt'});
  await flush();
  assert.match(h.get('import-summary').textContent, /20 \/ 21/);
  const next = h.take('/recordings/import');
  assert.equal(next.body.after, '20000101_120000_020.txt'); assert.equal(next.body.limit, 20);
  next.resolve({assets:[{id:'b'}],errors:[],skipped:[],processed:21,total:21,next_after:null});
  await work;
  assert.match(h.get('import-summary').textContent, /가져옴 2개/);
  assert.equal(h.get('asset-kind').value, 'device_recording');
  assert.equal(h.get('asset-library').open, true);
});

test('recording import never loops on a repeated cursor', async () => {
  const h = queuedHarness();
  const work = h.get('import-recordings').listeners.click();
  const batch = {assets:[],processed:20,total:40,next_after:'20000101_120000_020.txt'};
  h.take('/recordings/import').resolve(batch); await flush();
  h.take('/recordings/import').resolve(batch); await work;
  assert.equal(h.calls.filter(call => call.path === '/recordings/import').length, 2);
  assert.match(h.get('notice').textContent, /다음 묶음/);
});

test('initial asset load is replaced on device and subject changes; late responses cannot overwrite it', async () => {
  const h = queuedHarness({deferAssets:true});
  const initialWork = h.loadAssets(), initial = h.take('/assets');
  h.get('device').value = 'device-b';
  const subjectsWork = h.resetDevice(), deviceAssets = h.take('/assets');
  h.take('/subjects').resolve({device:'device-b',items:[supportedSubject]}); await subjectsWork;
  chooseSubject(h, supportedSubject.id); const subjectAssets = h.take('/assets');
  subjectAssets.resolve({items:[{id:'current-library'}]}); await flush();
  assert.equal(h.state.assetsLoaded, true); assert.equal(h.state.assets[0].id, 'current-library');
  initial.resolve({items:[{id:'stale-initial'}]}); await initialWork;
  deviceAssets.reject(new Error('stale device assets failure')); await flush();
  assert.equal(h.state.assetsLoaded, true); assert.equal(h.state.assets[0].id, 'current-library');
  assert.doesNotMatch(h.get('asset-state').textContent, /실패/);
  assert.equal(h.pending.length, 0);
});

test('TTS and upload carry explicit subject and discard stale completion UI', async () => {
  for (const upload of [false, true]) {
    const h = queuedHarness();
    h.get('asset-word-id').value = '30056'; h.get('asset-text').value = 'test speech';
    h.get('asset-file').files = [new Blob(['audio'])];
    const work = upload ? h.get('asset-form').listeners.submit({preventDefault() {}}) : h.get('generate-tts').listeners.click();
    const old = h.take(upload ? '/assets' : '/tts');
    assert.equal(upload ? old.body.get('subject') : old.body.subject, supportedSubject.id);
    chooseSubject(h, ''); chooseSubject(h, supportedSubject.id); await flush();
    h.get('asset-file').value = 'new-file'; h.get('asset-text').value = 'new-text';
    const count = h.calls.length;
    old.resolve({id:'stale-upload'}); await work;
    assert.equal(h.get('asset-file').value, 'new-file'); assert.equal(h.get('asset-text').value, 'new-text');
    assert.equal(h.calls.length, count); assert.doesNotMatch(h.get('notice').textContent, /완료/);
  }
});

test('active execution and execution requests reject environment changes and polling inventory replacement', async () => {
  for (const lock of [{active:{id:'live',status:'running'}}, {busy:true}, {pending:'검사 실행'}]) {
    const h = queuedHarness(); prepareCatalog(h); h.state.preview = {id:'keep-preview'};
    Object.assign(h.state, lock); h.sync();
    const generation = h.state.prepGeneration, catalog = h.state.catalog;
    h.get('device').value = 'device-b'; await h.resetDevice();
    h.get('subject').value = ''; h.resetSubject();
    h.updateDevices([{id:'device-b'}]);
    assert.equal(h.get('device').value, 'device-a'); assert.equal(h.get('subject').value, supportedSubject.id);
    assert.equal(h.state.prepGeneration, generation); assert.strictEqual(h.state.catalog, catalog);
    assert.equal(h.state.preview.id, 'keep-preview'); assert.equal(h.calls.length, 0);
    assert.equal(h.get('device').disabled, true); assert.equal(h.get('subject').disabled, true);
  }
});

test('status polling never overrides explicit subject with legacy static subjects', async () => {
  const h = queuedHarness();
  const work = h.refreshStatus();
  h.take('/status').resolve({busy:false,run:null,devices:[{id:'device-a'}],subjects:['JapanesePremium']}); await work;
  assert.equal(h.state.subject, supportedSubject.id); assert.equal(h.get('subject').value, supportedSubject.id);
  assert.equal(h.calls.filter(call => call.path === '/subjects').length, 0);
});
