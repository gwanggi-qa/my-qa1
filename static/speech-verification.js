(() => {
  'use strict';

  const BASE = '/api/speech-verification';
  const GRADES = ['Perfect', 'Excellent', 'Good', 'Bad', 'Fail'];
  const PAGE_SIZE = 25;
  const $ = id => document.getElementById(id);
  const token = document.querySelector('meta[name="qa-csrf-token"]')?.content || '';
  const state = {
    connected: false, busy: false, cleanup: false, pending: '', stopping: false,
    active: null, inspected: null, comparison: null, history: [], devices: [],
    device: '', subject: '', subjects: [], subjectsDevice: '', subjectsStatus: 'idle', subjectsError: '',
    prepGeneration: 0, pendingId: 0, pendingContext: null,
    catalog: [], catalogDevice: '', catalogSubject: '', assets: [], assetsLoaded: false,
    selected: new Set(), pairs: new Map(), preview: null, previewRequest: null,
    revision: 0, previewRevision: -1, catalogPage: 0, resultPage: 0,
    resultSignature: '', detailVersion: 0, compareVersion: 0, historyVersion: 0,
    assetsVersion: 0, statusPromise: null, timer: null, disposed: false,
    downloadUrls: new Set(), evidenceGeneration: 0
  };
  const labels = {
    passed: '통과', failed: '실패', blocked: '차단', unverified: '미검증',
    pending: '대기', queued: '대기', starting: '시작 중', running: '실행 중',
    stopping: '중지 중', stopped: '중지됨', interrupted: '중단됨',
    completed: '실행 종료', finished: '실행 종료', cancelled: '취소됨',
    unavailable: '검사 불가', unsupported: '미지원', review: '검토 필요', not_run: '미실행'
  };
  const reasons = {
    unsupported_build: '지원하지 않는 앱 빌드', untrusted_apk_path: '앱 설치 경로 검증 실패', supported_package_not_installed: '지원 앱이 설치되지 않음',
    supported_app_not_foreground: '지원 앱이 전면에 있지 않음', explicit_device_serial_required: '기기 선택 필요',
    attempt_timeout: '검사 시도 시간 초과', ui_xml_unavailable: '기기 화면 정보를 확인할 수 없음',
    'IllegalStateException: attempt_timeout': '검사 시도 시간 초과',
    argument_unavailable_buf: '인식 프레임의 음성 버퍼 인수를 확인할 수 없습니다.',
    'IllegalStateException: argument_unavailable_buf': '인식 프레임의 음성 버퍼 인수를 확인할 수 없습니다.',
    known_start_control_missing_or_ambiguous: '검사 시작 버튼을 특정할 수 없음',
    'Level-1 session mapping is not established': 'Level 1의 세션 연결 미검증',
    'Sentence session mapping is not established': '문장 세션 연결 미검증',
    'No verified SessionInfo mapping': '검증된 세션 정보 없음',
    'Missing recognition target': '음성 인식 대상 없음',
    'Missing companion TXT': '짝이 되는 TXT 파일 없음', 'Missing companion PCM': '짝이 되는 PCM 파일 없음',
    'Recording changed during import': '가져오는 동안 녹음 파일 변경됨',
    'Installed local OpenJTalk is unavailable; downloads are disabled': '로컬 OpenJTalk 사용 불가 · 자동 다운로드 비활성',
    'Local voice/dictionary missing; downloads are disabled': '로컬 음성 또는 사전 없음 · 자동 다운로드 비활성',
    'Unsupported unit/session identity': '지원하지 않는 단원 또는 세션',
    'Current speaking target does not exactly match the selected item': '현재 발화 대상과 선택 항목 불일치',
    'Session result acknowledged; whole-Stage completion has no verified UIXML indicator': '세션 결과 확인 · Stage 전체 완료 근거 미확인',
    'Stage session controls are not visible in the same stage container': '선택한 Stage 안에서 학습 세션 버튼을 확인할 수 없습니다.',
    'Stage alignment cannot make safe progress; requested session controls are not visible': '선택한 세션 버튼이 보이지 않아 Stage 위치를 더 이상 안전하게 조정할 수 없습니다.',
    'Missing semantic node bounds': '화면 요소의 위치를 확인할 수 없습니다.',
    'Semantic node is offscreen or has empty bounds': '대상 화면 요소가 화면 밖에 있거나 위치 정보가 없습니다.',
    'Missing or oversized UI XML': '화면 구조 정보가 없거나 허용 크기를 초과했습니다.',
    'Unexpected XML declarations': '화면 구조에 허용되지 않은 XML 선언이 있습니다.',
    'Malformed UI XML': '화면 구조 정보를 해석할 수 없습니다.',
    'Missing hierarchy root': '화면 구조의 최상위 요소가 없습니다.',
    'Wrong foreground subject/package': '다른 과목 또는 앱이 전면에 있습니다.',
    'Foreign/system overlay requires manual attention': '다른 앱 또는 시스템 창이 화면을 가리고 있습니다.',
    'Active Week identity is not uniquely exposed': '현재 Week를 하나로 특정할 수 없습니다.',
    'Unknown active Week label': '현재 Week 표시를 확인할 수 없습니다.',
    'Unknown Level label': '현재 Level 표시를 확인할 수 없습니다.',
    'Cancelled': '사용자가 중단했습니다.',
    'Navigation time limit reached': '학습 화면 이동 시간이 초과되었습니다.',
    'Host ADB serial does not match the requested device': '미러링 기기와 선택한 기기가 다릅니다.',
    'Expected Japanese Premium build 11': '일본어 프리미엄 빌드 11이 필요합니다.',
    'Wrong or missing subject': '과목이 다르거나 과목 정보가 없습니다.',
    'Item package/build mismatch': '검사 항목과 앱 패키지 또는 빌드가 다릅니다.',
    'Complete level/week/stage/session/word identity is required': 'Level·Week·Stage·세션·WordID 정보가 모두 필요합니다.',
    'Level-1 session selectors are not established': 'Level 1의 세션 이동 경로가 검증되지 않았습니다.',
    'Missing exact display text': '대조할 정확한 화면 문구가 없습니다.',
    'Missing exact recognition target including terminator': '종료 구분자를 포함한 정확한 인식 대상이 없습니다.',
    'Item is not navigable': '이 항목의 학습 화면 이동이 지원되지 않습니다.',
    'Session ordinal/label mismatch': '세션 순서와 표시 이름이 일치하지 않습니다.',
    'Session/kind mismatch': '세션과 학습 항목 유형이 일치하지 않습니다.',
    'Navigation input limit reached': '화면 이동 입력 횟수 한도에 도달했습니다.',
    'Semantic control is disabled': '대상 버튼이 비활성화되어 있습니다.',
    'Screen changed before input; no input sent': '입력 직전에 화면이 바뀌어 입력하지 않았습니다.',
    'Semantic control is not clickable': '대상 화면 요소를 누를 수 없습니다.',
    'No unique production stage scroller': 'Stage 목록의 스크롤 영역을 특정할 수 없습니다.',
    'Visible stage range is unknown': '화면에 보이는 Stage 범위를 확인할 수 없습니다.',
    'Start/transition control is not enabled and clickable': '시작 또는 이동 버튼을 누를 수 없습니다.',
    'Screen changed during arming handoff': '음성 입력 준비 중 화면이 바뀌었습니다.',
    'Menu navigation made no progress': '메뉴 이동 후 화면에 변화가 없습니다.',
    'Inactive Week has no accessible number or DB title': '선택할 Week의 번호 또는 DB 제목을 확인할 수 없습니다.',
    'Week selection did not confirm exact Level/Week': 'Week 선택 후 정확한 Level·Week를 확인하지 못했습니다.',
    'Unrecognized lesson start/resume state': '학습 시작 또는 이어하기 상태를 확인할 수 없습니다.',
    'Stage session label mismatch': '선택한 Stage와 세션 표시가 일치하지 않습니다.',
    'Unexpected screen after session selection': '세션 선택 후 예상하지 못한 화면이 나타났습니다.',
    'Unit identity changed after session selection': '세션 선택 후 단원 정보가 바뀌었습니다.',
    'Screen does not expose exact unit identity; navigate from the unit menu first': '현재 화면에서 단원을 확인할 수 없습니다. 단원 메뉴 확인이 필요합니다.',
    'Return-to-menu made no progress': '메뉴 복귀 후 화면에 변화가 없습니다.',
    'Unknown result acknowledgement': '결과 확인 버튼을 특정할 수 없습니다.',
    'Unknown screen; safe return-to-menu is unavailable': '현재 화면을 식별하지 못해 메뉴로 복귀하지 않았습니다.',
    'Return-to-menu action limit reached': '메뉴 복귀 동작 횟수 한도에 도달했습니다.',
    'Unsupported speaking screen': '지원하지 않는 발화 화면입니다.',
    'Current speaking target is not uniquely exposed': '현재 발화 대상을 하나로 특정할 수 없습니다.',
    'Speaking target is hidden in a custom view; UIXML cannot verify it': '발화 대상이 사용자 정의 화면 안에 있어 화면 구조로 검증할 수 없습니다.',
    'Mode must be selected or unit': '검사 범위 값이 올바르지 않습니다.',
    'Whole-unit navigation cannot cross the selected Stage': '단원 전체 검사는 선택한 Stage를 벗어날 수 없습니다.',
    'Target matches but paused/start state is not semantically exposed': '발화 대상은 일치하지만 대기 또는 시작 상태를 확인할 수 없습니다.',
    'Skipping a speaking target cannot establish whole-unit completion': '발화 항목을 건너뛰면 단원 전체 완료를 확인할 수 없습니다.',
    'No verified unit/session context': '검증된 단원 또는 세션 정보가 없습니다.',
    'Result belongs to a different or unsupported session': '결과가 다른 세션 또는 지원하지 않는 세션에 속합니다.',
    'Result screen lacks selected-item/session evidence': '결과 화면에 선택 항목 또는 세션의 확인 근거가 없습니다.',
    'No unique session completion acknowledgement': '세션 완료 확인 버튼을 특정할 수 없습니다.',
    'Session completion did not return to the selected unit': '세션 완료 후 선택한 단원으로 돌아오지 않았습니다.',
    stopped: '사용자가 중단했습니다.', device_not_ready: '기기가 연결되지 않았거나 사용 가능한 상태가 아닙니다.',
    unsafe_ui_xml: '화면 구조에 허용되지 않은 내용이 있습니다.', wrong_foreground_package: '다른 앱이 전면에 있습니다.',
    foreign_overlay: '다른 앱 또는 시스템 창이 화면을 가리고 있습니다.',
    scoped_start_selector_required: '선택 단원에 연결된 시작 버튼 정보가 필요합니다.',
    selector_subject_mismatch: '시작 버튼의 과목이 일치하지 않습니다.', selector_scope_incomplete: '시작 버튼의 단원 범위 정보가 불완전합니다.',
    selector_scope_mismatch: '시작 버튼과 선택한 단원 범위가 다릅니다.', unsupported_session: '지원하지 않는 학습 세션입니다.',
    selector_screen_changed: '시작 버튼 확인 후 화면이 바뀌었습니다.', unsupported_armed_transition: '지원하지 않는 입력 준비 화면 전환입니다.',
    ambiguous_play_control: '학습 시작 버튼을 하나로 특정할 수 없습니다.', menu_scope_mismatch: '현재 메뉴와 선택한 단원 범위가 다릅니다.',
    stage_not_unique: 'Stage를 하나로 특정할 수 없습니다.', stage_selector_not_scoped: 'Stage 버튼의 단원 범위를 확인할 수 없습니다.',
    intro_scope_missing: '학습 안내 화면의 단원 정보가 없습니다.', semantic_selector_not_unique: '대상 화면 요소를 하나로 특정할 수 없습니다.',
    selector_bounds_missing: '대상 버튼의 위치 정보가 없습니다.', selector_offscreen: '대상 버튼이 화면 밖에 있습니다.',
    cannot_verify_process_identity: '실행 프로세스의 동일성을 검증할 수 없습니다.',
    cannot_read_process_status: '프로세스 상태를 읽을 수 없습니다.', cannot_read_process_birth: '프로세스 시작 시점을 확인할 수 없습니다.',
    single_base_apk_required: '단일 기본 APK 설치 구성이 필요합니다.', apk_hash_unavailable: '설치된 APK의 해시를 확인할 수 없습니다.',
    lease_busy: '기기 작업 소유권이 사용 중입니다.', device_adapter_busy: '기기 입력 어댑터가 사용 중입니다.',
    no_owned_lease: '이 어댑터가 소유한 연결 기록이 없습니다.', lease_serial_mismatch: '연결 기록의 기기가 일치하지 않습니다.',
    invalid_lease_state: '연결 기록의 상태가 올바르지 않습니다.', invalid_lease_process_identity: '연결 기록의 프로세스 정보가 올바르지 않습니다.',
    owned_owner_still_alive: '기기 작업을 소유한 프로세스가 아직 실행 중입니다.', owned_helper_still_alive: '음성 입력 보조 프로세스가 아직 실행 중입니다.',
    helper_launch_unconfirmed: '보조 프로세스의 실행 여부를 확인할 수 없습니다.', invalid_owned_forward: '소유한 포트 연결 정보가 올바르지 않습니다.',
    forward_list_unverifiable: '기기 포트 연결 목록을 검증할 수 없습니다.', owned_forward_still_present: '소유한 기기 포트 연결이 남아 있습니다.',
    forward_allocation_unconfirmed: '기기 포트 연결 할당 상태를 확인할 수 없습니다.', lease_changed_during_cleanup_check: '정리 확인 중 연결 소유권 기록이 바뀌었습니다.',
    owned_resources_absent_lease_cleared: '소유한 프로세스와 포트 연결이 없음을 확인하고 연결 기록을 정리했습니다.',
    owned_resources_absent_lease_retained: '소유한 프로세스와 포트 연결은 없지만 연결 기록은 남아 있습니다.',
    owned_lease_exists_check_cleanup_and_review: '이전 연결 기록이 남아 있어 정리 확인과 검토가 필요합니다.', lease_ownership_changed: '기기 연결 소유권이 바뀌었습니다.',
    start_control_changed: '입력 직전에 시작 버튼이 바뀌었습니다.', known_intro_next_not_unique: '안내 화면의 다음 버튼을 특정할 수 없습니다.',
    intro_step_limit: '안내 화면 이동 횟수 한도에 도달했습니다.', capture_invalid_png: '기기 화면 캡처가 올바른 PNG 형식이 아닙니다.',
    grade_roi_missing_or_ambiguous: '앱 등급 표시 영역이 없거나 하나로 특정되지 않습니다.', grade_roi_bounds_missing: '앱 등급 표시 영역의 위치를 확인할 수 없습니다.',
    exact_runtime_target_required: '실행 중 대조할 정확한 인식 대상이 필요합니다.', invalid_slot: '음성 입력 위치 값이 올바르지 않습니다.',
    actual_word_id_unavailable: '실행 중인 앱의 실제 WordID를 검증할 수 없습니다.', timeout_s_must_be_5_to_300: '검사 제한 시간은 5~300초여야 합니다.',
    adb_did_not_return_allocated_port: 'ADB에서 할당된 포트 번호를 확인할 수 없습니다.', forward_ownership_changed_not_removed: '포트 연결 소유권이 바뀌어 제거하지 않았습니다.',
    conversation_requires_sequence: '회화는 순차 입력이 필요하여 현재 직접 입력 방식으로 검사할 수 없습니다.',
    expected_raw_pcm16le_mono_16000hz_max_15s: '15초 이하의 16 kHz·모노·16-bit PCM 음원이 필요합니다.',
    raw_pcm_required_not_audio_container: '오디오 컨테이너가 아닌 원시 PCM 입력이 필요합니다.', apk_changed_since_inspect: '사전 확인 후 설치된 APK가 바뀌었습니다.',
    previous_grade_visible: '이전 검사의 등급이 아직 화면에 남아 있습니다.', app_changed_after_attach: '입력 연결 후 앱 프로세스 또는 APK가 바뀌었습니다.',
    probe_exited_without_result: '입력 확인 프로세스가 결과 없이 종료되었습니다.', observation_capture_missing: '판정 시점의 화면 캡처가 없습니다.',
    conversation_grade_count_does_not_match_checkpoint: '회화 등급 표시 개수가 예상 판정 단계와 달라 해당 시도의 등급을 확인할 수 없습니다.',
    current_conversation_bubble_not_identified: '현재 시도의 회화 말풍선을 특정할 수 없습니다.',
    capture_missing_or_crossed_next_recording: '판정 화면 캡처가 없거나 다음 녹음 시작과 겹쳐 해당 시도의 등급을 확인할 수 없습니다.',
    cleanup_incomplete: '기기 입력 연결의 정리가 완료되지 않았습니다.', invalid_grade_roi: '앱 등급 표시 영역이 올바르지 않습니다.',
    local_ocr_grade_inconclusive: '로컬 화면 인식으로 앱 등급을 확정하지 못했습니다.'
  };
  const reasonPatterns = [
    [/^command_failed:/, '기기 명령 실행에 실패했습니다.'],
    [/^ADB operation failed:/, 'ADB 명령 실행에 실패했습니다.'],
    [/^Input failed:/, '화면 입력에 실패했습니다.'],
    [/^evidence_write_failed:/, '검사 증거를 저장하지 못했습니다.'],
    [/^local_ocr_unavailable:/, '로컬 화면 인식 도구를 사용할 수 없습니다.'],
    [/^probe_exit_code_-?\d+$/, '음성 입력 확인 프로세스가 오류로 종료되었습니다.'],
    [/^Expected exactly one .+; found \d+$/, '대상 화면 요소가 없거나 여러 개여서 특정할 수 없습니다.'],
    [/^Missing semantic ancestor /, '대상 화면 요소의 상위 구조를 확인할 수 없습니다.'],
    [/^No unambiguous safe .+ control$/, '검증된 이동 또는 재시도 버튼을 하나로 특정할 수 없습니다.'],
    [/^[\w.-]+_not_on_PATH$/, '필요한 로컬 실행 도구가 PATH에 없습니다.'],
    [/^[A-Z_]+_missing_[\w.-]+$/, '로컬 실행 도구의 설정 경로에 필요한 파일이 없습니다.']
  ];

  function text(value, fallback = '-') {
    if (value === null || value === undefined || value === '') return fallback;
    return typeof value === 'object' ? JSON.stringify(value) : String(value);
  }
  function list(value) { return Array.isArray(value) ? value : []; }
  function element(tag, value, className) {
    const node = document.createElement(tag);
    if (value !== undefined) node.textContent = text(value, '');
    if (className) node.className = className;
    return node;
  }
  function option(value, label) { return new Option(text(label, ''), text(value, '')); }
  function wordKey(item) { return text(item?.word_id, ''); }
  function deviceId(device) {
    return text(typeof device === 'object' && device ? device.id ?? device.serial ?? device.device : device, '');
  }
  function deviceName(device) {
    if (typeof device !== 'object' || !device) return text(device);
    const id = deviceId(device);
    const name = device.name || device.model || device.label;
    return name && String(name) !== id ? text(name) + ' · ' + id : id || text(device);
  }
  function mode() { return $('mode-unit').checked ? 'unit' : 'selected'; }
  function cleanupDevice() { return deviceId(state.active?.device) || $('device').value; }
  function unitCompletion(run) { return run?.unit_completed === true ? '단원 완료 확인' : run?.unit_completed === false ? '단원 미완료' : '단원 완료 미확인'; }
  function running(run) { return ['pending', 'queued', 'starting', 'running', 'stopping'].includes(run?.status); }
  function locked() { return !!state.pending || state.busy || running(state.active); }
  function executionLocked() { return state.busy || running(state.active) || !!state.pending && !state.pendingContext; }
  function preparationContext() { return {generation: state.prepGeneration, device: state.device, subject: state.subject}; }
  function preparationCurrent(context) {
    return !state.disposed && !executionLocked() && context.generation === state.prepGeneration &&
      context.device === state.device && context.device === $('device').value &&
      context.subject === state.subject && context.subject === $('subject').value;
  }
  function selectedSubject() {
    return state.subjectsDevice === state.device && state.device === $('device').value && state.subject === $('subject').value
      ? state.subjects.find(subject => subject.id === state.subject) : null;
  }
  function supports(capability) {
    const subject = selectedSubject();
    return subject?.supported === true && subject.support_status === 'supported' && subject.capabilities?.[capability] === true;
  }
  function catalogCurrent() {
    return supports('catalog') && state.catalogDevice === state.device && state.catalogSubject === state.subject;
  }
  function displayRun() { return state.inspected || state.active; }
  function statusLabel(status) { return labels[status] || text(status, '미검증'); }
  function reasonText(value, includeRaw = true) {
    const raw = text(value, '');
    if (!raw) return '';
    const translated = (Object.hasOwn(reasons, raw) ? reasons[raw] : null) || reasonPatterns.find(([pattern]) => pattern.test(raw))?.[1];
    if (translated) return translated + (includeRaw ? ' (' + raw + ')' : '');
    if (/[가-힣]/.test(raw)) return raw;
    return includeRaw ? '서버 사유 · ' + raw : raw;
  }
  function renderReason(target, value) {
    const raw = text(value, '');
    const translated = reasonText(value, false);
    target.replaceChildren();
    if (!raw) return;
    target.append(element('div', translated, 'word-text'));
    if (raw !== translated) {
      const details = element('details');
      details.append(element('summary', '기술 상세'), element('pre', raw));
      target.append(details);
    }
  }
  function appScore(record) {
    const value = record?.app_score;
    if (typeof value !== 'number' && (typeof value !== 'string' || !value.trim())) return null;
    const score = Number(value);
    return Number.isFinite(score) ? score : null;
  }
  function unsupported(item) {
    return item.supported === false || item.unsupported === true || item.status === 'unsupported' || item.navigation_available === false;
  }
  function category(status) {
    const normalized = String(status || '').toLowerCase();
    if (['pass', 'passed'].includes(normalized)) return 'passed';
    if (['fail', 'failed'].includes(normalized)) return 'failed';
    if (['blocked', 'unsupported', 'unavailable'].includes(normalized)) return 'blocked';
    return 'unverified';
  }
  function tone(status) { return ({passed: 'good', failed: 'bad', blocked: 'warn', unverified: 'muted'})[category(status)]; }
  function notify(message, error = false) {
    $('notice').textContent = text(message, '');
    $('notice').className = 'notice ' + (error ? 'bad' : 'muted');
  }
  function errorText(data) {
    if (!data || typeof data !== 'object') return text(data, '');
    return text(data.error ?? data.message ?? data.detail ?? data, '');
  }

  // All server access, including blobs, stays same-origin and carries the page token.
  async function request(path, {body, multipart = false, blob = false, timeout = 30000} = {}) {
    if (!token || token === '__TOKEN__') throw new Error('페이지 인증 토큰이 주입되지 않았습니다.');
    const headers = {'X-QA-CSRF-Token': token};
    if (body !== undefined && !multipart) headers['Content-Type'] = 'application/json';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(BASE + path, {
        method: body === undefined ? 'GET' : 'POST', headers,
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        ...(body === undefined ? {} : {body: multipart ? body : JSON.stringify(body)})
      });
      if (!response.ok) {
        const raw = await response.text();
        let detail = raw;
        try { detail = errorText(JSON.parse(raw)); } catch { /* Preserve non-JSON server errors as text. */ }
        throw new Error('요청 실패 · HTTP ' + response.status + (detail ? ' · ' + reasonText(detail) : ''));
      }
      if (blob) return {blob: await response.blob(), disposition: response.headers.get('Content-Disposition') || ''};
      if (response.status === 204) return {};
      try { return await response.json(); }
      catch { throw new Error('서버 JSON 응답을 읽을 수 없습니다.'); }
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('응답 시간 초과 · 실행 여부는 서버 상태를 확인하세요.');
      if (error.name === 'TypeError') throw new Error('서버 연결 실패 · ' + error.message);
      throw error;
    } finally { clearTimeout(timer); }
  }
  async function action(name, work, context = null) {
    if (state.pending) return;
    const id = ++state.pendingId;
    state.pending = name;
    state.pendingContext = context;
    notify('요청 중 · ' + name);
    sync();
    try { await work(); }
    catch (error) { if (!context || preparationCurrent(context)) notify(error.message, true); }
    finally {
      if (id === state.pendingId) { state.pending = ''; state.pendingContext = null; sync(); }
    }
  }
  function emptyRow(target, count, message) {
    const row = element('tr');
    const cell = element('td', message, 'empty');
    cell.colSpan = count;
    row.append(cell);
    target.replaceChildren(row);
  }
  function cell(row, label) {
    const node = element('td');
    node.dataset.label = label;
    row.append(node);
    return node;
  }
  function check(label, checked, onChange) {
    const wrapper = element('label', undefined, 'check');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = checked;
    input.addEventListener('change', () => onChange(input.checked));
    wrapper.append(input, element('span', label));
    return {wrapper, input};
  }
  function pairFor(item) {
    const key = wordKey(item);
    if (!state.pairs.has(key)) state.pairs.set(key, item.kind === 'word'
      ? {asset_id: '', approved: false, expected_outcomes: [], expected_grades: [], expect_reject: false}
      : {asset_id: '', approved: true, expected_grades: ['Perfect'], expect_reject: false});
    return state.pairs.get(key);
  }
  function outcomeLabel(outcome) {
    return ({word_correct: '정답', word_wrong: '오답'})[outcome] || text(outcome);
  }
  function wordPolicyValid(policy) {
    const outcomes = list(policy.expected_outcomes);
    return outcomes.length === 1 && ['word_correct', 'word_wrong'].includes(outcomes[0]) &&
      !list(policy.expected_grades).length && policy.expect_reject !== true;
  }
  function policySummary(policy) {
    if (list(policy.expected_outcomes).length) return '기대 단어 결과 · ' + policy.expected_outcomes.map(outcomeLabel).join(', ');
    return policy.expect_reject === true ? '인식 거부 기대' : '기대 등급 · ' + (list(policy.expected_grades).join(', ') || '미지정');
  }
  function scopeItems() {
    return state.catalog.filter(item => ['level', 'week', 'stage'].every(key => !$(key).value || text(item[key], '') === $(key).value));
  }
  function visibleItems() {
    const query = $('catalog-search').value.trim().toLowerCase();
    return scopeItems().filter(item => !query || [item.word_id, item.text, item.target].some(value => text(value, '').toLowerCase().includes(query)));
  }
  function uniqueWords(items) {
    const found = new Set();
    return items.filter(item => {
      const key = wordKey(item);
      if (!key) return true;
      if (found.has(key)) return false;
      found.add(key);
      return true;
    });
  }
  function targets() {
    return uniqueWords(mode() === 'unit' ? scopeItems() : state.catalog.filter(item => state.selected.has(wordKey(item))));
  }
  function localIssues() {
    const issues = [];
    if (!selectedSubject()) issues.push('과목 선택 필요');
    else if (!supports('input') || !supports('verdict')) issues.push('선택 과목의 음성 입력 또는 판정 미지원');
    if (!catalogCurrent()) issues.push('선택 기기·과목의 DB 목록 미조회');
    if (mode() === 'unit' && ['level', 'week', 'stage'].some(key => !$(key).value)) issues.push('단원 전체: Level · Week · Stage 지정 필요');
    const items = targets();
    if (!items.length) issues.push('검사 대상 없음');
    if (!state.assetsLoaded) issues.push('음원 목록 미확인');
    for (const item of items) {
      const key = wordKey(item);
      if (!key) { issues.push('WordID 없음 · ' + text(item.text)); continue; }
      const pair = pairFor(item);
      if (unsupported(item)) issues.push('WordID ' + key + ' · 미지원 · ' + reasonText(item.navigation_reason || item.reason || item.kind));
      if (!pair.asset_id || !state.assets.some(asset => text(asset.id, '') === pair.asset_id)) issues.push('WordID ' + key + ' · 입력 음원 없음');
      if (item.kind === 'word') {
        if (!wordPolicyValid(pair)) issues.push('WordID ' + key + ' · 단어 정답/오답 기준 미검증');
        if (pair.approved !== true) issues.push('WordID ' + key + ' · 단어 기대 결과 승인 필요');
      } else if (pair.approved && !pair.expect_reject && !pair.expected_grades.length) issues.push('WordID ' + key + ' · 승인한 기대 판정이 비어 있습니다.');
    }
    return issues;
  }
  function invalidate() {
    state.revision += 1;
    state.preview = null;
    state.previewRequest = null;
    state.previewRevision = -1;
    $('run-approved').checked = false;
    renderPreview();
    sync();
  }
  function serverIssues() {
    if (!state.preview) return [];
    const value = state.preview.issues;
    return Array.isArray(value) ? value : value ? [value] : [];
  }
  function issueText(issue) {
    if (typeof issue !== 'object' || !issue) return text(issue);
    const prefix = issue.word_id === undefined ? '' : 'WordID ' + text(issue.word_id) + ' · ';
    const missing = list(issue.missing_word_ids);
    return prefix + reasonText(issue.message ?? issue.reason ?? issue.error ?? issue.code ?? issue) + (missing.length ? ' · 누락 WordID: ' + missing.join(', ') : '');
  }
  function previewConsistency() {
    if (!state.preview || !state.previewRequest) return [];
    const issues = [];
    if (!state.preview.id) issues.push('서버 미리보기 ID 없음');
    if (deviceId(state.preview.device) !== state.previewRequest.device) issues.push('서버 미리보기 기기 불일치 또는 미확인');
    if (state.preview.subject !== undefined && state.preview.subject !== state.previewRequest.subject) issues.push('서버 미리보기 과목 불일치 또는 미확인');
    const returned = new Set(list(state.preview.items).map(wordKey));
    const expected = new Set(state.previewRequest.items.map(wordKey));
    if (returned.has('') || returned.size !== expected.size || [...expected].some(id => !returned.has(id))) issues.push('서버 미리보기의 WordID 범위가 요청과 다릅니다.');
    return issues;
  }
  function previewReady() {
    return state.preview?.ready === true && state.previewRevision === state.revision &&
      localIssues().length === 0 && previewConsistency().length === 0 &&
      serverIssues().every(issue => issue && typeof issue === 'object' && (issue.blocking === false || issue.blocking !== true && ['warning', 'info'].includes(issue.severity)));
  }
  function renderPreview() {
    const issues = [...localIssues(), ...previewConsistency(), ...serverIssues().map(issueText)];
    $('preview-issues').replaceChildren(...issues.map(issue => element('li', issue)));
    const ready = previewReady();
    $('ready-state').textContent = ready ? '서버 준비 완료' : state.preview ? '실행 불가 · 검토 필요' : '미확인';
    $('ready-state').className = 'badge ' + (ready ? 'good' : 'warn');
    $('preview-summary').textContent = state.preview
      ? '미리보기 ' + text(state.preview.id) + ' · 기기 ' + deviceName(state.preview.device) + ' · ' + list(state.preview.items).length + '개 · ready: ' + text(state.preview.ready)
      : targets().length + '개 대상 · ' + issues.length + '개 검토 사항 · 서버 미리보기 없음';
    $('preview-details').hidden = !state.preview;
    $('preview-items').replaceChildren(...list(state.preview?.items).map(item => {
      const node = element('div', undefined, 'preview-row');
      const policy = item.policy || item;
      node.append(element('strong', 'WordID ' + text(item.word_id)), element('div', item.text || item.target || ''),
        element('small', '음원 ' + text(item.asset_id) + ' · ' + policySummary(policy) + ' · ' + (policy.approved === true ? '승인됨' : '검토 필요')),
        element('small', [item.status, item.reason, item.issues && text(item.issues)].filter(Boolean).map(value => text(value)).join(' · ')));
      return node;
    }));
  }
  function fillFilters(from = 'level') {
    const names = ['level', 'week', 'stage'];
    for (const key of names.slice(names.indexOf(from))) {
      const previous = $(key).value;
      const parents = names.slice(0, names.indexOf(key));
      const items = state.catalog.filter(item => parents.every(parent => !$(parent).value || text(item[parent], '') === $(parent).value));
      const values = [...new Set(items.map(item => text(item[key], '')).filter(Boolean))].sort((a, b) => a.localeCompare(b, undefined, {numeric: true}));
      $(key).replaceChildren(option('', '전체'), ...values.map(value => option(value, value)));
      if (values.includes(previous)) $(key).value = previous;
    }
  }
  function renderCatalog() {
    const items = visibleItems();
    const unit = mode() === 'unit';
    const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
    state.catalogPage = Math.min(state.catalogPage, pages - 1);
    $('catalog-page').textContent = (state.catalogPage + 1) + ' / ' + pages;
    $('catalog-prev').disabled = state.catalogPage === 0;
    $('catalog-next').disabled = state.catalogPage >= pages - 1;
    $('selection-count').textContent = targets().length + (unit ? '개 · 단원 전체' : '개 선택');
    $('scope-state').textContent = scopeItems().length + '개 DB 항목 · 검색 ' + items.length + '개';
    const selectable = uniqueWords(items).filter(wordKey);
    const selectedCount = selectable.filter(item => state.selected.has(wordKey(item))).length;
    $('select-visible').checked = unit || (selectable.length > 0 && selectedCount === selectable.length);
    $('select-visible').indeterminate = !unit && selectedCount > 0 && selectedCount < selectable.length;
    $('select-visible').disabled = unit || !selectable.length;
    const body = $('catalog-rows');
    body.replaceChildren();
    for (const item of items.slice(state.catalogPage * PAGE_SIZE, (state.catalogPage + 1) * PAGE_SIZE)) {
      const row = element('tr');
      const key = wordKey(item);
      const pair = pairFor(item);
      const selection = cell(row, '선택');
      selection.className = 'select-cell';
      const box = check('선택', unit || state.selected.has(key), checked => {
        if (checked) state.selected.add(key); else state.selected.delete(key);
        invalidate(); renderCatalog();
      });
      box.input.disabled = unit || !key;
      box.input.setAttribute('aria-label', 'WordID ' + key + ' 선택');
      selection.append(box.input);
      const info = cell(row, 'DB 항목');
      info.append(element('code', 'WordID ' + (key || '없음')), element('div', item.text, 'word-text'));
      if (item.target && item.target !== item.text) info.append(element('small', '대상 · ' + text(item.target)));
      info.append(element('small', [item.level, item.week, item.stage].map(value => text(value)).join(' / ')), element('small', 'Session ' + text(item.session) + ' · ' + text(item.kind)));
      if (unsupported(item)) info.append(element('span', '미지원', 'badge warn'), element('small', reasonText(item.navigation_reason || item.reason)));
      const assetCell = cell(row, '입력 음원');
      const select = document.createElement('select');
      select.setAttribute('aria-label', 'WordID ' + key + ' 입력 음원');
      select.append(option('', '원본 음원 가져오기 · 미연결'));
      const assets = [...state.assets].sort((a, b) => Number(wordKey(b) === key) - Number(wordKey(a) === key));
      for (const asset of assets) select.append(option(asset.id, text(asset.name || asset.id) + ' · WordID ' + text(asset.word_id) + ' · ' + text(asset.kind)));
      select.value = pair.asset_id;
      select.disabled = !key;
      const assetMeta = element('small');
      const gradesCell = cell(row, '기대 판정');
      const grades = element('div', undefined, 'grades');
      grades.setAttribute('role', 'group');
      grades.setAttribute('aria-label', 'WordID ' + key + (item.kind === 'word' ? ' 기대 단어 결과' : ' 기대 등급'));
      const gradeInputs = [];
      const approvalCell = cell(row, '승인');
      const approval = check('승인', pair.approved, value => { pair.approved = value; updateApproval(); invalidate(); });
      const approvalState = element('small');
      const updateApproval = () => {
        const asset = state.assets.find(entry => text(entry.id, '') === pair.asset_id);
        assetMeta.textContent = asset ? text(asset.text) + ' · ' + text(asset.kind) + (wordKey(asset) && wordKey(asset) !== key ? ' · 다른 WordID 음원' : '') : (item.native_available === true ? '원본 음원을 가져오면 자동 연결됩니다.' : (item.native_reason || '검증된 원본 음원 연결이 없습니다. 등록 음원을 선택하세요.'));
        const valid = item.kind === 'word' ? wordPolicyValid(pair) : pair.expect_reject || pair.expected_grades.length;
        approval.input.disabled = !key || !valid;
        approval.input.checked = pair.approved;
        approvalState.textContent = !valid && item.kind === 'word' ? '단어 기준 미검증' : pair.approved ? '승인됨' : '검토 필요';
        approvalState.className = pair.approved && valid ? 'good' : 'warn';
      };
      const changed = () => { updateApproval(); invalidate(); };
      select.addEventListener('change', () => { pair.asset_id = select.value; changed(); });
      assetCell.append(select, assetMeta);
      const nativeButton = element('button', '원본 음원 가져오기');
      nativeButton.type = 'button';
      nativeButton.className = 'primary';
      nativeButton.disabled = item.native_available !== true || !key || !catalogCurrent() || !supports('native');
      nativeButton.title = item.native_available === true ? 'WordID ' + key + ' 원본 음원 가져오기' : (item.native_reason || '검증된 원본 음원 없음');
      nativeButton.addEventListener('click', () => {
        if (locked() || !state.connected || item.native_available !== true || !catalogCurrent() || !supports('native')) return;
        const context = preparationContext();
        void action('원본 음원 가져오기', async () => {
          const asset = await request('/native', {body: {device: context.device, subject: context.subject, word_id: item.word_id}, timeout: 120000});
          if (!preparationCurrent(context)) return;
          try {
            await loadAssets(context);
            if (!preparationCurrent(context)) return;
            connectNative(item, asset);
            notify('WordID ' + key + ' 원본 음원 연결 완료');
          }
          catch (error) { if (preparationCurrent(context)) notify('원본 음원 등록 완료 · 연결 확인 실패: ' + error.message, true); }
        }, context);
      });
      const nativeActions = element('div', undefined, 'actions');
      nativeActions.append(nativeButton);
      assetCell.append(nativeActions);
      if (item.kind === 'word') {
        if (!wordPolicyValid(pair)) grades.append(element('small',
          list(pair.expected_grades).length || pair.expect_reject ? policySummary(pair) + ' · 단어 기준 미검증' : '기대 단어 결과 미선택', 'warn'));
        for (const outcome of ['word_correct', 'word_wrong']) {
          const choice = check(outcomeLabel(outcome), wordPolicyValid(pair) && pair.expected_outcomes[0] === outcome, checked => {
            if (!checked) return;
            pair.expected_outcomes = [outcome];
            pair.expected_grades = [];
            pair.expect_reject = false;
            pair.approved = false;
            invalidate(); renderCatalog();
          });
          choice.input.type = 'radio';
          choice.input.name = 'word-outcome-' + key;
          choice.input.value = outcome;
          choice.input.disabled = !key;
          grades.append(choice.wrapper);
        }
        gradesCell.append(grades);
      } else {
        for (const grade of GRADES) {
          const gradeBox = check(grade, pair.expected_grades.includes(grade), checked => {
            pair.expected_grades = GRADES.filter(value => value === grade ? checked : pair.expected_grades.includes(value));
            changed();
          });
          gradeBox.input.disabled = pair.expect_reject || !key;
          gradeInputs.push(gradeBox.input);
          grades.append(gradeBox.wrapper);
        }
        const reject = check('인식 거부 기대', pair.expect_reject, checked => {
          pair.expect_reject = checked;
          pair.expected_grades = [];
          gradeInputs.forEach(input => { input.checked = false; input.disabled = checked || !key; });
          changed();
        });
        reject.input.disabled = !key;
        gradesCell.append(grades, reject.wrapper);
      }
      const approvalGroup = element('div', undefined, 'approval');
      approvalGroup.append(approval.wrapper, approvalState);
      approvalCell.append(approvalGroup);
      updateApproval();
      body.append(row);
    }
    if (!items.length) emptyRow(body, 5, state.catalog.length ? '조건에 맞는 DB 항목 없음' : 'DB 목록 없음');
  }
  function renderAssets() {
    $('asset-count').textContent = state.assets.length;
    const body = $('asset-rows');
    body.replaceChildren();
    const query = $('asset-search').value.trim().toLowerCase();
    const assets = state.assets.filter(asset => (!$('asset-kind').value || asset.kind === $('asset-kind').value) &&
      (!query || [asset.name, asset.word_id, asset.text].some(value => text(value, '').toLowerCase().includes(query))));
    for (const asset of assets) {
      const row = element('tr');
      cell(row, '음원 / WordID').append(element('div', asset.name || asset.id), element('code', 'WordID ' + text(asset.word_id)));
      const sourceNames = {native: '앱 원본 음원', 'local-tts': '로컬 TTS', device_recording: '음성인식 입력 기록 · 발화 출처 미확인', 'human-recording': '사람 녹음'};
      const description = cell(row, '문구 / 형식');
      description.append(element('div', asset.text), element('small', sourceNames[asset.kind] || asset.kind));
      if (asset.kind === 'device_recording') {
        const matches = state.assets.filter(other => other.sha256 && other.sha256 === asset.sha256 && ['native', 'local-tts'].includes(other.kind));
        if (matches.length) description.append(element('small', '동일 PCM 확인: ' + [...new Set(matches.map(other => sourceNames[other.kind]))].join(', ')));
        if (asset.input_record) description.append(element('small', '내부 인식 상태: ' + text(asset.input_record.recogState) + ' · 앱 화면 등급은 실행 결과에서 확인'));
      }
      const duration = asset.duration_s !== null && asset.duration_s !== undefined && Number.isFinite(Number(asset.duration_s)) ? Number(asset.duration_s).toFixed(2) + ' s' : '-';
      cell(row, '길이 / SHA-256').append(element('div', duration), element('code', asset.sha256));
      body.append(row);
    }
    if (!assets.length) emptyRow(body, 3, '조건에 맞는 등록 음원 없음');
  }
  function connectNative(item, asset) {
    const stored = state.assets.find(entry => text(entry.id, '') === text(asset?.id, ''));
    if (!stored || stored.kind !== 'native' || wordKey(stored) !== wordKey(item) ||
        stored.subject && stored.subject !== state.subject || asset?.subject && asset.subject !== state.subject) {
      throw new Error('가져온 원본 음원의 문장 연결을 확인할 수 없습니다.');
    }
    pairFor(item).asset_id = text(stored.id, '');
    invalidate(); renderCatalog();
  }
  function nativeImportItems() {
    if (!catalogCurrent()) return [];
    const word = $('asset-word-id').value.trim();
    // Never expand unit mode into an implicit whole-catalog import.
    return uniqueWords(scopeItems().filter(item => word ? wordKey(item) === word : state.selected.has(wordKey(item))));
  }
  async function importNative() {
    if (locked() || !state.connected || !catalogCurrent() || !supports('native')) return;
    const items = nativeImportItems();
    if (!items.length) { notify('현재 DB 범위의 WordID를 입력하거나 항목을 선택하세요.', true); return; }
    if (items.some(item => item.native_available !== true)) {
      notify('선택 항목에 검증된 앱 원본 음원이 없습니다.', true); return;
    }
    const context = preparationContext();
    await action('기기 음원 가져오기', async () => {
      for (const item of items) {
        if (!preparationCurrent(context)) return;
        const asset = await request('/native', {body: {device: context.device, subject: context.subject, word_id: item.word_id}, timeout: 120000});
        if (!preparationCurrent(context)) return;
        await loadAssets(context);
        if (!preparationCurrent(context)) return;
        connectNative(item, asset);
      }
      notify('앱 원본 음원 ' + items.length + '개 연결 완료');
    }, context);
  }
  async function loadAssets(context = preparationContext()) {
    if (!preparationCurrent(context)) return;
    const version = ++state.assetsVersion;
    try {
      const data = await request('/assets');
      if (version !== state.assetsVersion || !preparationCurrent(context)) return;
      if (!Array.isArray(data.items)) throw new Error('음원 목록 응답에 items가 없습니다.');
      const next = data.items.filter(item => item && item.id !== undefined);
      const changed = JSON.stringify(state.assets) !== JSON.stringify(next);
      for (const pair of state.pairs.values()) {
        if (!pair.asset_id) continue;
        const before = state.assets.find(asset => text(asset.id, '') === pair.asset_id);
        const after = next.find(asset => text(asset.id, '') === pair.asset_id);
        if (JSON.stringify(before) !== JSON.stringify(after)) pair.approved = false;
        if (!after) pair.asset_id = '';
      }
      state.assets = next;
      state.assetsLoaded = true;
      if (changed) invalidate();
      $('asset-state').textContent = next.length + '개 등록 음원';
      renderAssets(); renderCatalog(); sync();
    } catch (error) {
      if (version !== state.assetsVersion || !preparationCurrent(context)) return;
      state.assetsLoaded = false;
      $('asset-state').textContent = '음원 목록 확인 실패 · ' + error.message;
      invalidate();
      throw error;
    }
  }
  async function loadCatalog() {
    if (locked() || !supports('catalog') || !state.connected) return;
    const context = preparationContext();
    await action('DB 목록 조회', async () => {
      const {device, subject} = context;
      state.catalog = []; state.catalogDevice = ''; state.catalogSubject = ''; state.selected.clear(); state.pairs.clear();
      invalidate(); renderCatalog();
      const data = await request('/catalog', {body: {device, subject}, timeout: 120000});
      if (!preparationCurrent(context)) return;
      if (!Array.isArray(data.items)) throw new Error('DB 목록 응답에 items가 없습니다.');
      if (deviceId(data.device) !== device) throw new Error('DB 목록의 기기가 요청과 다릅니다.');
      if (data.subject !== subject) throw new Error('DB 목록의 과목이 요청과 다릅니다.');
      state.catalog = data.items;
      state.catalogDevice = device;
      state.catalogSubject = subject;
      state.catalogPage = 0;
      fillFilters(); renderCatalog(); renderPreview();
      $('catalog-state').textContent = data.items.length + '개 DB 항목 · ' + deviceName(data.device);
      notify('DB 목록 조회 완료');
    }, context);
  }
  function scopeValue(key) {
    const value = $(key).value;
    return value ? state.catalog.find(item => text(item[key], '') === value)?.[key] ?? value : null;
  }
  function previewBody() {
    return {
      device: state.device, subject: state.subject, mode: mode(),
      items: targets().map(item => {
        const pair = pairFor(item);
        const asset = state.assets.find(entry => text(entry.id, '') === pair.asset_id);
        return {word_id: item.word_id, asset_id: asset?.id ?? null, approved: pair.approved === true,
          ...(item.kind === 'word' && wordPolicyValid(pair)
            ? {expected_outcomes: [...pair.expected_outcomes]}
            : {expected_grades: [...pair.expected_grades], expect_reject: pair.expect_reject === true})};
      }),
      level: scopeValue('level'), week: scopeValue('week'), stage: scopeValue('stage')
    };
  }
  async function createPreview() {
    if (locked() || !state.connected || !catalogCurrent() || !supports('input') || !supports('verdict') || !targets().length) return;
    const context = preparationContext();
    await action('검사 미리보기', async () => {
      invalidate();
      const revision = state.revision;
      const body = previewBody();
      const data = await request('/preview', {body, timeout: 120000});
      if (!preparationCurrent(context) || revision !== state.revision) {
        await discardPreview(data.id);
        return;
      }
      state.preview = data; state.previewRequest = body; state.previewRevision = revision;
      renderPreview();
      notify(previewReady() ? '서버 준비 완료 · 실행 승인 대기' : '미리보기 검토 필요');
    }, context);
  }

  function resultEntries(run) {
    if (!run) return [];
    const results = list(run.results);
    const seen = new Set(results.map(wordKey).filter(Boolean));
    return [...results, ...uniqueWords(list(run.items)).filter(item => !wordKey(item) || !seen.has(wordKey(item))).map(item => ({...item, status: 'unverified', reason: '서버 결과 없음', attempts: []}))];
  }
  function counts(run) {
    const totals = {passed: 0, failed: 0, blocked: 0, unverified: 0};
    resultEntries(run).forEach(result => { totals[category(result.status)] += 1; });
    return totals;
  }
  function renderCounts(id, run) {
    const totals = counts(run);
    $(id).replaceChildren(...Object.keys(totals).map(key => {
      const wrapper = element('div');
      wrapper.append(element('dt', labels[key]), element('dd', totals[key], tone(key)));
      return wrapper;
    }));
  }
  function metadata(target, pairs) {
    target.replaceChildren(...pairs.map(([name, value]) => {
      const entry = element('div');
      entry.append(element('dt', name), element('dd', value));
      return entry;
    }));
  }
  function renderActive() {
    const run = state.active;
    $('run-state').textContent = run ? statusLabel(run.status) : state.busy ? '서버 사용 중' : '실행 없음';
    metadata($('run-meta'), run ? [
      ['실행 ID', run.id], ['기기', deviceName(run.device)], ['범위', run.mode === 'unit' ? '단원 전체' : text(run.mode === 'selected' ? '선택 항목' : run.mode)],
      ['시작', run.started_at], ['종료', run.finished_at], ['소스', text(run.source)],
      ['Level / Week / Stage', [run.scope?.level, run.scope?.week, run.scope?.stage].map(value => text(value)).join(' / ')],
      ['단원 완료', run.unit_completed === true ? '완료 확인' : run.unit_completed === false ? '미완료' : '미확인'],
      ['진행 단계', run.phase], ['현재 항목', run.current_index ? text(run.current_index) + ' · ' + text(run.current_text) : '-']
    ] : []);
    const total = resultEntries(run).length;
    const reported = list(run?.results).filter(result => ['passed', 'pass', 'failed', 'fail', 'blocked', 'unverified', 'unavailable', 'unsupported'].includes(result.status)).length;
    $('run-progress').max = Math.max(1, total);
    $('run-progress').value = reported;
    $('run-progress-text').textContent = run ? '서버 판정 수집 ' + reported + ' / ' + total + '개' : '실행 없음';
    renderReason($('run-reason'), run?.reason || run?.error || run?.completion_evidence?.reason);
    renderCounts('live-summary', run);
  }
  function cleanupText(value) {
    if (!value || Array.isArray(value) && !value.length) return '';
    return '기기 정리 필요 · ' + (value === true ? '정리 상태 확인 전 실행 차단' : text(value));
  }
  function updateDevices(devices) {
    const previous = $('device').value;
    state.devices = list(devices);
    // Polling must not replace an execution's selected environment, even if it disconnects.
    if (executionLocked()) return;
    const desired = state.devices.map(device => ({id: deviceId(device), name: deviceName(device), offline: device?.online === false || device?.connected === false || ['offline', 'unauthorized', 'disconnected'].includes(device?.status || device?.state)}));
    const signature = JSON.stringify(desired);
    if ($('device').dataset.signature === signature) return;
    $('device').dataset.signature = signature;
    $('device').replaceChildren(option('', '기기 선택'), ...desired.filter(device => device.id).map(device => {
      const node = option(device.id, device.name + (device.offline ? ' · 연결 불가' : ''));
      node.disabled = device.offline;
      return node;
    }));
    $('device').value = '';
    if (desired.some(device => device.id === previous && !device.offline)) $('device').value = previous;
    if ($('device').value !== previous) resetDevice();
  }
  function subjectStatus(subject) {
    return ({supported: '지원', unsupported_build: '미지원 빌드', not_integrated: '연동 미지원', unavailable: '확인 불가'})[subject.support_status] || '확인 불가';
  }
  function renderSubjects() {
    const loading = state.subjectsStatus === 'loading';
    const placeholder = loading ? '설치된 등록 과목 확인 중' : '과목 선택';
    $('subject').replaceChildren(option('', placeholder), ...state.subjects.map(subject =>
      option(subject.id, subject.name + ' · ' + subjectStatus(subject))));
    $('subject').value = state.subject;
    $('subject').setAttribute('aria-busy', String(loading));
    const selected = selectedSubject();
    $('subject-state').textContent = !state.device ? '기기 선택 필요'
      : loading ? '설치된 등록 과목 확인 중…'
      : state.subjectsStatus === 'error' ? '설치된 등록 과목 확인 실패 · ' + state.subjectsError
      : selected ? [selected.name, subjectStatus(selected), selected.package, selected.build == null ? '' : '빌드 ' + selected.build, reasonText(selected.reason, false)].filter(Boolean).join(' · ')
      : state.subjectsStatus === 'ready' && !state.subjects.length ? '설치된 등록 과목 없음'
      : '설치된 등록 과목 ' + state.subjects.length + '개 · 과목 선택 필요';
    $('subject-state').className = 'notice ' + (state.subjectsStatus === 'error' ? 'bad' : selected && !supports('catalog') ? 'warn' : 'muted');
    sync();
  }
  async function loadSubjects() {
    if (!state.device || executionLocked()) return;
    const context = preparationContext();
    state.subjectsStatus = 'loading'; state.subjectsError = '';
    renderSubjects();
    try {
      const data = await request('/subjects', {body: {device: context.device}, timeout: 120000});
      if (!preparationCurrent(context)) return;
      if (deviceId(data.device) !== context.device || !Array.isArray(data.items)) throw new Error('설치된 등록 과목 응답이 요청과 다릅니다.');
      state.subjects = data.items.filter(subject => subject && typeof subject.id === 'string' && subject.id);
      state.subjectsDevice = context.device;
      state.subjectsStatus = 'ready';
    } catch (error) {
      if (!preparationCurrent(context)) return;
      state.subjects = []; state.subjectsDevice = '';
      state.subjectsStatus = 'error'; state.subjectsError = error.message;
    }
    renderSubjects();
  }
  async function refreshStatus() {
    if (state.statusPromise) return state.statusPromise;
    state.statusPromise = (async () => {
      try {
        const data = await request('/status', {timeout: 10000});
        if (typeof data.busy !== 'boolean' || !Object.prototype.hasOwnProperty.call(data, 'run')) throw new Error('서버 상태 응답의 busy 또는 run이 없습니다.');
        const oldId = state.active?.id;
        const oldStatus = state.active?.status;
        const wasBusy = state.busy;
        const wasExecuting = state.busy || running(state.active);
        state.connected = true; state.busy = data.busy; state.active = data.run || null;
        if (!wasExecuting && (state.busy || running(state.active))) cancelPreparation();
        state.cleanup = !!cleanupText(data.cleanup_required);
        $('connection-state').textContent = data.busy ? '서버 사용 중' : '서버 연결됨';
        $('connection-state').className = 'badge ' + (data.busy ? 'warn' : 'good');
        $('connection-error').hidden = true;
        $('cleanup-notice').textContent = cleanupText(data.cleanup_required);
        $('cleanup-notice').hidden = !state.cleanup;
        updateDevices(data.devices);
        if (state.device && state.subjectsStatus === 'idle' && !executionLocked()) void loadSubjects();
        if (state.cleanup || data.busy || oldId !== state.active?.id) {
          if (state.preview) invalidate();
        }
        if (!data.busy && !running(state.active)) state.stopping = false;
        if (oldId !== state.active?.id || oldStatus !== state.active?.status || data.busy) $('resume-approved').checked = false;
        renderActive(); renderResults(); sync();
        if (oldId !== state.active?.id || oldStatus !== state.active?.status || wasBusy && !data.busy) void loadHistory().catch(() => {});
      } catch (error) {
        state.connected = false;
        $('connection-state').textContent = '상태 확인 실패';
        $('connection-state').className = 'badge warn';
        $('connection-error').textContent = error.message;
        $('connection-error').hidden = false;
        $('run-approved').checked = false; $('resume-approved').checked = false;
        sync();
      }
    })();
    try { await state.statusPromise; } finally { state.statusPromise = null; }
  }
  async function poll() {
    if (state.disposed) return;
    await refreshStatus();
    if (!state.disposed) state.timer = setTimeout(poll, 2000);
  }
  async function discardPreview(id) {
    if (!id) return;
    try { await request('/prepare/reset', {body: {preview_id: id}}); }
    catch { /* A busy server may retain it; local invalidation still prevents reuse. */ }
  }
  function cancelPreparation() {
    state.prepGeneration += 1;
    state.assetsVersion += 1;
    if (state.pendingContext) {
      state.pendingId += 1; state.pending = ''; state.pendingContext = null;
    }
    if (state.subjectsStatus === 'loading') { state.subjectsStatus = 'idle'; renderSubjects(); }
  }
  function resetPreparation() {
    void discardPreview(state.preview?.id);
    cancelPreparation();
    state.catalog = []; state.catalogDevice = ''; state.catalogSubject = ''; state.selected.clear(); state.pairs.clear();
    state.catalogPage = 0;
    for (const id of ['level', 'week', 'stage', 'catalog-search', 'asset-word-id', 'asset-text', 'asset-file']) $(id).value = '';
    $('mode-selected').checked = true; $('mode-unit').checked = false;
    $('import-report').hidden = true; $('import-report').open = false;
    $('import-summary').textContent = ''; $('import-issues').replaceChildren();
    $('notice').textContent = '';
    $('catalog-state').textContent = 'DB 목록 미조회';
    fillFilters(); invalidate(); renderCatalog();
  }
  function resetDevice() {
    if (executionLocked()) { $('device').value = state.device; sync(); return; }
    const device = $('device').value;
    if (device === state.device) return;
    state.device = device; state.subject = ''; $('subject').value = '';
    state.subjects = []; state.subjectsDevice = ''; state.subjectsStatus = 'idle'; state.subjectsError = '';
    resetPreparation(); renderSubjects();
    if (device) {
      refreshPreparationAssets();
      return loadSubjects();
    }
  }
  function resetSubject() {
    if (executionLocked()) { $('subject').value = state.subject; sync(); return; }
    const subject = $('subject').value;
    if (subject === state.subject) return;
    if (subject && !state.subjects.some(entry => entry.id === subject)) { $('subject').value = state.subject; return; }
    state.subject = subject;
    resetPreparation(); renderSubjects();
    refreshPreparationAssets();
  }
  function refreshPreparationAssets() {
    const context = preparationContext();
    void loadAssets(context).catch(error => { if (preparationCurrent(context)) notify(error.message, true); });
  }
  function runFromResponse(data) {
    const run = data?.run || data;
    return run && typeof run === 'object' && run.id && run.status ? run : null;
  }
  async function execute(path, body) {
    await action(path === '/resume' ? '검사 재개' : '검사 실행', async () => {
      invalidate();
      $('resume-approved').checked = false;
      try {
        const data = await request(path, {body, timeout: 120000});
        const run = runFromResponse(data);
        if (run) { state.active = run; state.busy = running(run); }
        state.inspected = null; state.detailVersion += 1; state.resultSignature = '';
        activateTab('execution'); renderActive(); renderResults();
        notify('실행 요청 접수 · 서버 상태 확인 중');
      } finally {
        state.connected = false;
        // A timed-out mutation is never retried automatically.
        await refreshStatus();
        void loadHistory().catch(() => {});
      }
    });
  }

  function fileEntries(files) {
    if (!files) return [];
    if (typeof files === 'string') return [{file: files, label: files}];
    if (Array.isArray(files)) return files.flatMap(file => fileEntries(file));
    if (typeof files !== 'object') return [];
    const filename = files.file ?? files.filename ?? files.path ?? files.name;
    if (typeof filename === 'string') return [{file: filename, label: files.label || filename, mime: files.mime || files.content_type}];
    return Object.entries(files).flatMap(([label, value]) => typeof value === 'string' ? [{file: value, label: label + ' · ' + value}] : fileEntries(value));
  }
  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    state.downloadUrls.add(url);
    const link = element('a');
    link.href = url; link.download = filename;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => { URL.revokeObjectURL(url); state.downloadUrls.delete(url); }, 60000);
  }
  function evidenceControl(runId, entry) {
    const container = element('div', undefined, 'file-control');
    const button = element('button', '증거 다운로드 · ' + text(entry.label));
    button.type = 'button';
    const errorNode = element('small', '', 'evidence-error bad');
    const generation = state.evidenceGeneration;
    button.addEventListener('click', async () => {
      button.disabled = true; errorNode.textContent = '';
      try {
        const response = await request('/runs/' + encodeURIComponent(runId) + '/files/' + encodeURIComponent(entry.file), {blob: true});
        if (generation !== state.evidenceGeneration || state.disposed) return;
        downloadBlob(response.blob, entry.file.split(/[\\/]/).pop() || 'evidence');
        button.disabled = false;
      } catch (error) { errorNode.textContent = error.message; button.disabled = false; }
    });
    container.append(button, errorNode);
    return container;
  }
  function attemptDetails(run, result) {
    const details = element('details');
    const attempts = list(result.attempts);
    details.append(element('summary', attempts.length + '회 시도'));
    for (const [index, attempt] of attempts.entries()) {
      const section = element('section', undefined, 'attempt');
      section.append(element('h3', '시도 ' + (index + 1)));
      const meta = element('dl', undefined, 'meta');
      const fields = [['앱 단어 결과', outcomeLabel(attempt.app_outcome)], ['앱 등급', attempt.app_grade], ['엔진 상태', attempt.engine_state]];
      if (appScore(attempt) !== null) fields.splice(1, 0, ['앱 점수', appScore(attempt)]);
      metadata(meta, fields);
      const reason = element('div');
      renderReason(reason, attempt.reason);
      section.append(meta, reason, element('small', '전체 입력'), element('pre', attempt.full_input, 'full-input'), element('small', '엔진 내부 점수'), element('pre', typeof attempt.scores === 'object' ? JSON.stringify(attempt.scores, null, 2) : text(attempt.scores)));
      for (const phase of list(attempt.word_phases)) {
        const phaseDetails = element('details');
        phaseDetails.append(element('summary', '단어 입력 ' + text(phase.word_phase) + '단계 · ' + outcomeLabel(phase.app_outcome)));
        phaseDetails.append(element('p', '전체 음성 전달 · ' + (phase.full_input === true ? '확인' : '미확인')),
          element('pre', JSON.stringify({reason: phase.reason, scores: phase.scores}, null, 2)));
        section.append(phaseDetails);
      }
      for (const file of fileEntries(attempt.files)) section.append(evidenceControl(result.inherited_from || run.id, file));
      details.append(section);
    }
    if (!attempts.length) details.append(element('p', '서버 시도 기록 없음', 'muted'));
    for (const file of fileEntries(result.files)) details.append(evidenceControl(result.inherited_from || run.id, file));
    return details;
  }
  function lastGrade(result) { return list(result?.attempts).at(-1)?.app_grade; }
  function lastOutcome(result) { return list(result?.attempts).at(-1)?.app_outcome; }
  function appendResultStatus(target, result) {
    if (!result) { target.append(element('span', '미검증 · 기록 없음', 'muted')); return; }
    const group = category(result.status);
    target.append(element('span', labels[group], 'badge ' + tone(result.status)));
    if (statusLabel(result.status) !== labels[group]) target.append(element('small', '서버 상태 · ' + text(result.status)));
    const policy = result.policy || result;
    target.append(element('small', '앱 등급 · ' + text(lastGrade(result))));
    target.append(element('small', '앱 단어 결과 · ' + outcomeLabel(lastOutcome(result))));
    const score = appScore(list(result.attempts).at(-1)) ?? appScore(result);
    if (score !== null) target.append(element('small', '앱 점수 · ' + score));
    const reason = element('div');
    renderReason(reason, result.reason);
    target.append(
      element('small', policySummary(policy)),
      element('small', policy.approved === true ? '기준 승인됨' : '기준 검토 필요'), reason);
  }
  function comparisonRows(run, comparison) {
    const primary = resultEntries(run);
    const other = resultEntries(comparison);
    const byId = new Map(other.filter(wordKey).map(result => [wordKey(result), result]));
    const seen = new Set(primary.map(wordKey).filter(Boolean));
    return [...primary.map(result => ({result, compared: byId.get(wordKey(result))})), ...other.filter(result => !wordKey(result) || !seen.has(wordKey(result))).map(compared => ({result: null, compared}))];
  }
  function renderResults(force = false) {
    const run = displayRun();
    const comparison = state.comparison;
    const signature = JSON.stringify([run, comparison, $('result-filter').value, state.resultPage]);
    if (!force && signature === state.resultSignature) return;
    state.resultSignature = signature;
    state.evidenceGeneration += 1;
    renderCounts('result-summary', run);
    $('result-context').textContent = run ? (state.inspected ? '이력' : '현재 실행') + ' · ' + text(run.id) + ' · ' + statusLabel(run.status) + ' · ' + deviceName(run.device) + ' · ' + text(run.started_at) + (run.mode === 'unit' ? ' · ' + unitCompletion(run) : '') : '선택된 실행 없음';
    const rows = comparisonRows(run, comparison).filter(entry => !$('result-filter').value || category(entry.result?.status) === $('result-filter').value);
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    state.resultPage = Math.min(state.resultPage, pages - 1);
    $('results-page').textContent = (state.resultPage + 1) + ' / ' + pages;
    $('results-prev').disabled = state.resultPage === 0;
    $('results-next').disabled = state.resultPage >= pages - 1;
    const body = $('result-rows'); body.replaceChildren();
    for (const entry of rows.slice(state.resultPage * PAGE_SIZE, (state.resultPage + 1) * PAGE_SIZE)) {
      const item = entry.result || entry.compared;
      const row = element('tr');
      cell(row, 'WordID / 문구').append(element('code', 'WordID ' + text(item.word_id)), element('div', item.text, 'word-text'));
      appendResultStatus(cell(row, '선택 실행'), entry.result);
      const otherCell = cell(row, '비교 실행');
      if (comparison) {
        appendResultStatus(otherCell, entry.compared);
        if (entry.result && entry.compared) {
          const changed = entry.result.status !== entry.compared.status || lastGrade(entry.result) !== lastGrade(entry.compared) || lastOutcome(entry.result) !== lastOutcome(entry.compared);
          otherCell.append(element('small', changed ? '판정 / 등급 / 단어 결과 차이 있음' : '판정 / 등급 / 단어 결과 동일', changed ? 'warn' : 'muted'));
          if (entry.result.text !== entry.compared.text) otherCell.append(element('small', '비교 문구 · ' + text(entry.compared.text)));
        }
      } else otherCell.append(element('span', '비교 없음', 'muted'));
      const attempts = cell(row, '시도 / 증거');
      if (entry.result && run) attempts.append(attemptDetails(run, entry.result));
      if (entry.compared && comparison) {
        const details = attemptDetails(comparison, entry.compared);
        details.firstChild.textContent = '비교 실행 · ' + list(entry.compared.attempts).length + '회 시도';
        attempts.append(details);
      }
      body.append(row);
    }
    if (!rows.length) emptyRow(body, 4, run ? '조건에 맞는 결과 없음' : '선택된 실행 없음');
    sync();
  }
  function historyLabel(run) { return text(run.started_at || run.id) + ' · ' + text(run.id) + ' · ' + statusLabel(run.status); }
  function renderHistory() {
    const body = $('history-rows'); body.replaceChildren();
    for (const run of state.history) {
      const row = element('tr');
      cell(row, '실행 / 시작 시각').append(element('code', run.id), element('small', run.started_at));
      cell(row, '기기 / 범위').append(element('div', deviceName(run.device)), element('small', run.mode === 'unit' ? '단원 전체' : text(run.mode === 'selected' ? '선택 항목' : run.mode)));
      const statusCell = cell(row, '상태');
      statusCell.append(element('span', statusLabel(run.status), 'badge'));
      if (run.mode === 'unit') statusCell.append(element('small', unitCompletion(run)));
      const control = cell(row, '상세');
      const button = element('button', '결과 열기'); button.type = 'button';
      button.addEventListener('click', () => inspectRun(run.id));
      control.append(button); body.append(row);
    }
    if (!state.history.length) emptyRow(body, 4, '실행 이력 없음');
    const selected = $('compare-run').value;
    $('compare-run').replaceChildren(option('', '비교 없음'), ...state.history.map(run => option(run.id, historyLabel(run))));
    if (state.comparison && !state.history.some(run => String(run.id) === String(state.comparison.id))) $('compare-run').append(option(state.comparison.id, historyLabel(state.comparison)));
    $('compare-run').value = selected;
  }
  async function loadHistory() {
    const version = ++state.historyVersion;
    try {
      const data = await request('/history');
      if (!Array.isArray(data.runs)) throw new Error('실행 이력 응답에 runs가 없습니다.');
      if (version !== state.historyVersion) return;
      state.history = data.runs.filter(run => run?.id);
      $('history-state').textContent = state.history.length + '개 실행';
      renderHistory();
    } catch (error) {
      if (version !== state.historyVersion) return;
      $('history-state').textContent = '이력 확인 실패 · ' + error.message;
      throw error;
    }
  }
  async function inspectRun(id) {
    const version = ++state.detailVersion;
    notify('실행 이력 조회 중 · ' + id);
    try {
      const data = await request('/runs/' + encodeURIComponent(id));
      if (version !== state.detailVersion) return;
      const run = runFromResponse(data);
      if (!run || String(run.id) !== String(id)) throw new Error('실행 상세 ID가 요청과 다릅니다.');
      state.inspected = run; state.resultPage = 0;
      renderResults(true); activateTab('results'); notify('이력 조회 완료');
    } catch (error) { if (version === state.detailVersion) notify(error.message, true); }
  }
  async function selectComparison() {
    const id = $('compare-run').value;
    const version = ++state.compareVersion;
    state.comparison = null;
    $('compare-state').textContent = id ? '비교 실행 조회 중' : '';
    renderResults(true);
    if (!id) return;
    try {
      const data = await request('/runs/' + encodeURIComponent(id));
      if (version !== state.compareVersion) return;
      const run = runFromResponse(data);
      if (!run || String(run.id) !== id) throw new Error('비교 실행 ID가 요청과 다릅니다.');
      state.comparison = run; state.resultPage = 0;
      $('compare-state').textContent = '비교 · ' + historyLabel(run);
      renderResults(true);
    } catch (error) { if (version === state.compareVersion) $('compare-state').textContent = error.message; }
  }
  function sync() {
    const unavailable = !state.connected || locked();
    $('device').disabled = !state.connected || executionLocked();
    $('subject').disabled = !state.connected || executionLocked() || state.subjectsStatus !== 'ready' || !state.subjects.length;
    const loadingLabels = {
      'DB 목록 조회': 'DB 목록 불러오는 중…',
      '입력 기록 가져오기': '음성인식 입력 기록 불러오는 중…',
      '기기 음원 가져오기': '앱 원본 음원 불러오는 중…',
      '원본 음원 가져오기': '원본 음원 불러오는 중…',
      '음원 등록': '음원 등록 중…',
      'TTS 생성': 'TTS 생성 중…',
      '음원 목록 조회': '음원 목록 불러오는 중…'
    };
    const loadingText = loadingLabels[state.pending] || '';
    $('prepare-loading').hidden = !loadingText;
    $('prepare-loading-text').textContent = loadingText;
    $('prepare-fields').setAttribute('aria-busy', String(!!loadingText));
    $('asset-loading').hidden = !loadingText || state.pending === 'DB 목록 조회';
    $('asset-loading-text').textContent = loadingText;
    $('load-catalog').textContent = state.pending === 'DB 목록 조회' ? '불러오는 중…' : 'DB 목록 조회';
    $('load-catalog').setAttribute('aria-busy', String(state.pending === 'DB 목록 조회'));
    $('import-native').textContent = state.pending === '기기 음원 가져오기' ? '불러오는 중…' : '기기 음원 가져오기';
    $('import-native').setAttribute('aria-busy', String(state.pending === '기기 음원 가져오기'));
    $('import-native').disabled = unavailable || !catalogCurrent() || !supports('native');
    $('import-recordings').textContent = state.pending === '입력 기록 가져오기' ? '불러오는 중…' : '음성인식 입력 기록 가져오기';
    $('import-recordings').setAttribute('aria-busy', String(state.pending === '입력 기록 가져오기'));
    $('prepare-fields').disabled = !!state.pending || state.busy || running(state.active);
    $('load-catalog').disabled = unavailable || !supports('catalog');
    $('import-recordings').disabled = unavailable || !supports('input');
    $('upload-asset').disabled = unavailable || !supports('input');
    $('generate-tts').disabled = unavailable || !supports('input');
    $('refresh-assets').disabled = !!state.pending;
    $('preview-button').disabled = unavailable || !catalogCurrent() || !supports('input') || !supports('verdict') || !targets().length;
    const ready = previewReady() && !unavailable && !state.cleanup;
    $('run-approved').disabled = !ready;
    $('start-run').disabled = !ready || !$('run-approved').checked;
    $('stop-run').disabled = !!state.pending || state.stopping || !(state.busy || running(state.active));
    const unfinished = list(state.active?.results).some(item => ['blocked', 'stopped', 'not_run'].includes(item.status));
    const canResume = state.connected && !locked() && !state.cleanup && state.active?.id && unfinished && ['blocked', 'stopped', 'interrupted', 'finished'].includes(state.active.status);
    $('resume-approved').disabled = !canResume;
    $('resume-run').disabled = !canResume || !$('resume-approved').checked;
    $('show-live-results').disabled = !state.active;
    $('current-results').disabled = !state.active;
    $('export-run').disabled = !!state.pending || !displayRun()?.id;
    $('refresh-history').disabled = !!state.pending;
    $('cleanup-controls').hidden = !state.cleanup;
    $('cleanup-check').disabled = !!state.pending || !state.connected || !cleanupDevice();
    $('cleanup-check').textContent = '기기 정리 상태 확인' + (cleanupDevice() ? ' · ' + cleanupDevice() : '');
  }
  function activateTab(name) {
    for (const value of ['prepare', 'execution', 'results']) {
      const selected = name === value;
      $('tab-' + value).setAttribute('aria-selected', String(selected));
      $('tab-' + value).tabIndex = selected ? 0 : -1;
      $('panel-' + value).hidden = !selected;
    }
  }
  function showCurrent() {
    state.detailVersion += 1; state.inspected = null; state.resultPage = 0;
    renderResults(true); activateTab('results');
  }
  function exportName(disposition, runId) {
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    const plain = /filename="([^"]+)"|filename=([^;]+)/i.exec(disposition);
    let name = 'speech-' + runId + '.zip';
    try { if (encoded) name = decodeURIComponent(encoded[1]); else if (plain) name = (plain[1] || plain[2]).trim(); } catch { /* Keep the fallback name. */ }
    return name.replace(/[\\/:*?"<>|\x00-\x1f]/g, '_');
  }
  function bindEvents() {
    const tabs = ['prepare', 'execution', 'results'];
    tabs.forEach((name, index) => {
      $('tab-' + name).addEventListener('click', () => activateTab(name));
      $('tab-' + name).addEventListener('keydown', event => {
        let next;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = tabs.length - 1;
        if (next !== undefined) { event.preventDefault(); activateTab(tabs[next]); $('tab-' + tabs[next]).focus(); }
      });
    });
    $('device').addEventListener('change', resetDevice);
    $('subject').addEventListener('change', resetSubject);
    $('cleanup-check').addEventListener('click', () => {
      if ($('cleanup-check').disabled || !state.cleanup) return;
      void action('기기 정리 상태 확인', async () => {
        $('cleanup-result').hidden = false;
        $('cleanup-result').textContent = '정리 상태 확인 중';
        try {
          const response = await request('/cleanup/check', {body: {device: cleanupDevice()}});
          const confirmed = response.cleanup_ok === true;
          $('cleanup-result').textContent = (confirmed ? '정리 확인 응답: 정상' : '정리 확인 응답: 미확인') + (response.reason ? ' · ' + reasonText(response.reason) : '');
          $('cleanup-result').className = 'notice ' + (confirmed ? 'good' : 'warn');
          notify('정리 확인 요청 완료 · 서버 상태 재조회');
        } catch (error) {
          $('cleanup-result').textContent = error.message;
          $('cleanup-result').className = 'notice bad';
          throw error;
        } finally {
          // Only /status may release the cleanup gate; this response never clears it locally.
          await refreshStatus();
        }
      });
    });
    $('load-catalog').addEventListener('click', loadCatalog);
    for (const key of ['level', 'week', 'stage']) $(key).addEventListener('change', () => {
      if (key === 'level') fillFilters('week');
      if (key === 'week') fillFilters('stage');
      state.selected.clear(); state.catalogPage = 0;
      invalidate(); renderCatalog();
    });
    for (const id of ['mode-selected', 'mode-unit']) $(id).addEventListener('change', () => { state.catalogPage = 0; invalidate(); renderCatalog(); });
    $('catalog-search').addEventListener('input', () => { state.catalogPage = 0; renderCatalog(); });
    $('select-visible').addEventListener('change', () => {
      for (const item of visibleItems()) {
        const key = wordKey(item);
        if (!key) continue;
        if ($('select-visible').checked) state.selected.add(key); else state.selected.delete(key);
      }
      invalidate(); renderCatalog();
    });
    $('catalog-prev').addEventListener('click', () => { state.catalogPage = Math.max(0, state.catalogPage - 1); renderCatalog(); });
    $('catalog-next').addEventListener('click', () => { state.catalogPage += 1; renderCatalog(); });
    $('results-prev').addEventListener('click', () => { state.resultPage = Math.max(0, state.resultPage - 1); renderResults(true); });
    $('results-next').addEventListener('click', () => { state.resultPage += 1; renderResults(true); });
    $('refresh-assets').addEventListener('click', () => {
      if (locked()) return;
      const context = preparationContext();
      return action('음원 목록 조회', async () => {
        await loadAssets(context);
        if (preparationCurrent(context)) notify('음원 목록 갱신 완료');
      }, context);
    });
    $('asset-form').addEventListener('submit', event => {
      event.preventDefault();
      if (locked() || !state.connected || !supports('input') || !$('asset-form').reportValidity()) return;
      const file = $('asset-file').files[0];
      if (!file) { notify('등록할 음원 파일이 없습니다.', true); $('asset-file').focus(); return; }
      const context = preparationContext();
      return action('음원 등록', async () => {
        const form = new FormData();
        form.append('file', file); form.append('text', $('asset-text').value); form.append('word_id', $('asset-word-id').value.trim());
        form.append('subject', context.subject);
        form.append('sample_rate', '16000'); form.append('channels', '1'); form.append('sample_width', '2');
        await request('/assets', {body: form, multipart: true, timeout: 120000});
        if (!preparationCurrent(context)) return;
        $('asset-file').value = '';
        try { await loadAssets(context); if (preparationCurrent(context)) notify('음원 등록 완료'); }
        catch (error) { if (preparationCurrent(context)) notify('음원 등록 완료 · 목록 갱신 실패: ' + error.message, true); }
      }, context);
    });
    $('generate-tts').addEventListener('click', () => {
      if (locked() || !state.connected || !supports('input') || !$('asset-form').reportValidity()) return;
      const context = preparationContext();
      return action('TTS 생성', async () => {
        await request('/tts', {body: {subject: context.subject, text: $('asset-text').value, word_id: $('asset-word-id').value.trim()}, timeout: 120000});
        if (!preparationCurrent(context)) return;
        try { await loadAssets(context); if (preparationCurrent(context)) notify('TTS 생성 완료'); }
        catch (error) { if (preparationCurrent(context)) notify('TTS 생성 완료 · 목록 갱신 실패: ' + error.message, true); }
      }, context);
    });
    $('import-native').addEventListener('click', importNative);
    $('asset-search').addEventListener('input', renderAssets);
    $('asset-kind').addEventListener('change', renderAssets);
    $('import-recordings').addEventListener('click', () => {
      if (locked() || !state.connected || !supports('input')) return;
      const context = preparationContext();
      return action('입력 기록 가져오기', async () => {
        const response = {assets: [], errors: [], skipped: []};
        let after = '';
        $('import-report').hidden = false; $('import-report').open = true;
        do {
          const batch = await request('/recordings/import', {body: {device: context.device, subject: context.subject, limit: 20, after}, timeout: 120000});
          if (!preparationCurrent(context)) return;
          for (const key of ['assets', 'errors', 'skipped']) response[key].push(...list(batch[key]));
          $('import-summary').textContent = '입력 기록 확인 중 · ' + text(batch.processed) + ' / ' + text(batch.total) + '건';
          const next = batch.next_after;
          if (next && (typeof next !== 'string' || next <= after)) throw new Error('입력 기록의 다음 묶음을 확인할 수 없습니다. 저장된 기록은 유지됩니다.');
          after = next;
        } while (after);
        const summary = '가져옴 ' + list(response.assets).length + '개 · 오류 ' + list(response.errors).length + '개 · 건너뜀 ' + list(response.skipped).length + '개';
        $('import-report').hidden = false;
        $('import-report').open = true;
        $('import-summary').textContent = summary;
        $('import-issues').replaceChildren(...[
          ...list(response.errors).map(item => element('li', '오류 · ' + text(item.filename) + ' · ' + issueText(item), 'bad')),
          ...list(response.skipped).map(item => element('li', '건너뜀 · ' + text(item.filename) + ' · ' + issueText(item), 'warn'))
        ]);
        try {
          await loadAssets(context);
          if (preparationCurrent(context)) {
            $('asset-kind').value = 'device_recording'; $('asset-search').value = '';
            $('asset-library').open = true; renderAssets();
            notify(summary, list(response.errors).length > 0);
          }
        }
        catch (error) { if (preparationCurrent(context)) notify('가져오기 완료 · 목록 갱신 실패: ' + error.message, true); }
      }, context);
    });
    $('preview-button').addEventListener('click', createPreview);
    $('run-approved').addEventListener('change', sync);
    $('start-run').addEventListener('click', () => {
      if ($('start-run').disabled || !previewReady() || !$('run-approved').checked || !state.connected || locked() || state.cleanup) return;
      void execute('/run', {preview_id: state.preview.id, subject: state.previewRequest.subject, approved: true});
    });
    $('resume-approved').addEventListener('change', sync);
    $('resume-run').addEventListener('click', () => {
      if ($('resume-run').disabled || !$('resume-approved').checked) return;
      void execute('/resume', {run_id: state.active.id, approved: true});
    });
    $('stop-run').addEventListener('click', () => {
      if ($('stop-run').disabled) return;
      void action('실행 중지', async () => {
        state.stopping = true; sync();
        try { await request('/stop', {body: {}}); notify('중지 요청 접수 · 서버 종료 확인 대기'); }
        catch (error) { state.stopping = false; throw error; }
        finally { await refreshStatus(); }
      });
    });
    $('show-live-results').addEventListener('click', showCurrent);
    $('current-results').addEventListener('click', showCurrent);
    $('result-filter').addEventListener('change', () => { state.resultPage = 0; renderResults(true); });
    $('refresh-history').addEventListener('click', () => action('실행 이력 조회', async () => { await loadHistory(); notify('이력 갱신 완료'); }));
    $('compare-run').addEventListener('change', selectComparison);
    $('clear-compare').addEventListener('click', () => { $('compare-run').value = ''; void selectComparison(); });
    $('export-run').addEventListener('click', () => {
      const run = displayRun();
      if (!run?.id) return;
      void action('결과 내보내기', async () => {
        const response = await request('/runs/' + encodeURIComponent(run.id) + '/export', {blob: true, timeout: 120000});
        downloadBlob(response.blob, exportName(response.disposition, run.id));
        notify('결과 파일 다운로드 요청 완료');
      });
    });
    window.addEventListener('pagehide', () => {
      state.disposed = true; clearTimeout(state.timer); state.evidenceGeneration += 1;
      for (const url of state.downloadUrls) URL.revokeObjectURL(url);
      state.downloadUrls.clear();
    });
    window.addEventListener('pageshow', event => { if (event.persisted) { state.disposed = false; state.resultSignature = ''; renderResults(true); void poll(); } });
  }
  function initialize() {
    bindEvents(); renderAssets(); renderCatalog(); renderPreview(); renderActive(); renderResults(); sync();
    void poll();
    void loadAssets().catch(error => notify(error.message, true));
    void loadHistory().catch(error => notify(error.message, true));
  }
  initialize();
})();
