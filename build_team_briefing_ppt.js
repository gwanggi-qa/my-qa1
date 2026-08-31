const path = require('path');
const fs = require('fs/promises');
const pptxgen = require('C:/tmp/qa-pptx-tools/node_modules/pptxgenjs');
const sharp = require('C:/tmp/qa-pptx-tools/node_modules/sharp');

const ROOT = 'D:/QA/TEST';
const ASSET_DIR = path.join(ROOT, 'ppt_assets');
const CROPPED_DIR = path.join(ASSET_DIR, 'cropped');
const OUTPUT = path.join(ROOT, 'tablet_qa_automation_team_briefing.pptx');

const C = {
  charcoal: '171B22',
  panel: '222833',
  panel2: '2B3340',
  line: '3B4656',
  white: 'F6F8FB',
  text: '17202B',
  muted: '657287',
  light: 'F2F5F8',
  teal: '22B78A',
  blue: '3D65D7',
  orange: 'F4A62A',
  red: 'D95262',
};

const W = 13.333;
const H = 7.5;
const FONT = 'Malgun Gothic';
const SHAPE = {
  rect: 'rect',
  roundRect: 'roundRect',
  ellipse: 'ellipse',
  line: 'line',
};

function addText(slide, text, options = {}) {
  slide.addText(text, {
    fontFace: FONT,
    margin: 0,
    breakLine: false,
    fit: 'shrink',
    ...options,
  });
}

function addFooter(slide, page, dark = false) {
  const color = dark ? 'A8B2C1' : '7A8799';
  addText(slide, 'Tablet QA Automation | Team Briefing', {
    x: 0.5, y: 7.14, w: 5.0, h: 0.16, fontSize: 8.5, color,
  });
  addText(slide, String(page).padStart(2, '0'), {
    x: 12.25, y: 7.1, w: 0.55, h: 0.2, fontSize: 9, bold: true, color, align: 'right',
  });
}

function addTitle(slide, title, eyebrow, dark = false) {
  const titleColor = dark ? C.white : C.text;
  const eyebrowColor = dark ? '8D9CB2' : C.teal;
  addText(slide, eyebrow.toUpperCase(), {
    x: 0.55, y: 0.42, w: 3.0, h: 0.24, fontSize: 9, bold: true, color: eyebrowColor, charSpacing: 1.4,
  });
  addText(slide, title, {
    x: 0.55, y: 0.76, w: 11.9, h: 0.52, fontSize: 28, bold: true, color: titleColor,
  });
}

function card(slide, x, y, w, h, title, body, accent, dark = false) {
  const fill = dark ? C.panel : C.white;
  const titleColor = dark ? C.white : C.text;
  const bodyColor = dark ? 'C6CFDA' : C.muted;
  slide.addShape(SHAPE.roundRect, {
    x, y, w, h,
    rectRadius: 0.06,
    fill: { color: fill },
    line: { color: dark ? C.line : 'DCE3EA', width: 0.7 },
  });
  slide.addShape(SHAPE.rect, {
    x, y, w: 0.065, h,
    fill: { color: accent }, line: { color: accent },
  });
  addText(slide, title, {
    x: x + 0.25, y: y + 0.2, w: w - 0.45, h: 0.25, fontSize: 14, bold: true, color: titleColor,
  });
  addText(slide, body, {
    x: x + 0.25, y: y + 0.58, w: w - 0.45, h: h - 0.75, fontSize: 11.5, color: bodyColor,
    breakLine: false, valign: 'top', paraSpaceAfterPt: 7,
  });
}

function tag(slide, x, y, w, text, color) {
  slide.addShape(SHAPE.roundRect, {
    x, y, w, h: 0.34, rectRadius: 0.05,
    fill: { color, transparency: 8 }, line: { color, transparency: 100 },
  });
  addText(slide, text, {
    x: x + 0.08, y: y + 0.075, w: w - 0.16, h: 0.16, fontSize: 8.5, bold: true, color: C.white, align: 'center',
  });
}

function circleNumber(slide, x, y, number, label, color, dark = false) {
  slide.addShape(SHAPE.ellipse, {
    x, y, w: 0.5, h: 0.5,
    fill: { color }, line: { color },
  });
  addText(slide, String(number), {
    x, y: y + 0.1, w: 0.5, h: 0.18, fontSize: 13, bold: true, color: C.white, align: 'center',
  });
  addText(slide, label, {
    x: x - 0.18, y: y + 0.64, w: 0.86, h: 0.38, fontSize: 10, bold: true,
    color: dark ? C.white : C.text, align: 'center', valign: 'mid',
  });
}

async function cropUi(sourceName, outputName) {
  const input = path.join(ASSET_DIR, sourceName);
  const output = path.join(CROPPED_DIR, outputName);
  await sharp(input)
    .extract({ left: 0, top: 0, width: 1137, height: 640 })
    .png()
    .toFile(output);
  return output;
}

async function build() {
  await fs.mkdir(CROPPED_DIR, { recursive: true });
  const testCaseUi = await cropUi('testcase-ui.png', 'testcase-ui-crop.png');
  const randomUi = await cropUi('random-test-ui.png', 'random-test-ui-crop.png');
  const recorderUi = await cropUi('recorder-ui.png', 'recorder-ui-crop.png');

  const pptx = new pptxgen();
  pptx.layout = 'LAYOUT_WIDE';
  pptx.author = 'QA Automation Team';
  pptx.company = 'Internal Team';
  pptx.subject = 'Tablet QA automation prototype and AI roadmap';
  pptx.title = '바이브 코딩으로 만든 태블릿 자동 QA 솔루션';
  pptx.lang = 'ko-KR';
  pptx.theme = {
    headFontFace: FONT,
    bodyFontFace: FONT,
    lang: 'ko-KR',
  };

  // 1. Title
  {
    const slide = pptx.addSlide();
    slide.background = { color: C.charcoal };
    slide.addShape(pptx.ShapeType.rect, { x: 0, y: 0, w: W, h: H, fill: { color: C.charcoal }, line: { color: C.charcoal } });
    slide.addShape(pptx.ShapeType.rect, { x: 8.18, y: 0, w: 5.15, h: H, fill: { color: '10151D' }, line: { color: '10151D' } });
    slide.addImage({ path: testCaseUi, x: 7.85, y: 0.5, w: 5.0, h: 2.815, transparency: 10 });
    slide.addShape(pptx.ShapeType.rect, { x: 7.85, y: 0.5, w: 5.0, h: 2.815, fill: { color: C.charcoal, transparency: 48 }, line: { color: C.line, transparency: 50, width: 0.6 } });
    tag(slide, 0.65, 0.75, 1.55, 'TEAM BRIEFING', C.teal);
    addText(slide, '바이브 코딩으로 만든\n태블릿 자동 QA 솔루션', {
      x: 0.65, y: 1.38, w: 6.9, h: 1.35, fontSize: 31, bold: true, color: C.white, breakLine: false,
    });
    addText(slide, '실제 기기를 보며 반복 테스트하고,\nAI를 필요한 지점에만 단계적으로 더하는 QA 프로토타입', {
      x: 0.68, y: 3.02, w: 5.95, h: 0.8, fontSize: 15, color: 'C9D2DE', breakLine: false,
    });
    const statY = 4.65;
    [['실기기', 'ADB 연결'], ['반복', 'Recorder / Random'], ['확장', 'AI 선택 적용']].forEach((item, index) => {
      const x = 0.7 + index * 2.17;
      slide.addShape(pptx.ShapeType.roundRect, { x, y: statY, w: 1.92, h: 1.1, rectRadius: 0.07, fill: { color: C.panel }, line: { color: C.line, width: 0.6 } });
      addText(slide, item[0], { x: x + 0.18, y: statY + 0.2, w: 1.55, h: 0.25, fontSize: 15, bold: true, color: index === 0 ? C.teal : index === 1 ? C.blue : C.orange, align: 'center' });
      addText(slide, item[1], { x: x + 0.18, y: statY + 0.6, w: 1.55, h: 0.18, fontSize: 9.5, color: 'C9D2DE', align: 'center' });
    });
    addText(slide, '2026.07 | Internal Prototype', { x: 0.7, y: 6.72, w: 4.0, h: 0.2, fontSize: 10, color: '8D9CB2' });
    addFooter(slide, 1, true);
  }

  // 2. Problem and starting point
  {
    const slide = pptx.addSlide();
    slide.background = { color: C.light };
    addTitle(slide, '수동 QA의 반복 부담에서 출발했습니다', 'WHY NOW');
    addText(slide, '목표는 완성형 테스트 플랫폼이 아니라, 실제 기기를 대상으로 빠르게 검증 가능한 초기 모델을 만드는 것이었습니다.', {
      x: 0.55, y: 1.42, w: 10.8, h: 0.28, fontSize: 14, color: C.muted,
    });
    card(slide, 0.62, 2.0, 3.85, 2.55, '반복 테스트의 피로', '같은 화면 전환과 버튼 동작을\n여러 번 수동으로 확인해야 합니다.', C.red);
    card(slide, 4.74, 2.0, 3.85, 2.55, '관찰과 기록의 분리', '기기 화면을 보면서\n테스트 결과를 별도로 정리해야 합니다.', C.orange);
    card(slide, 8.86, 2.0, 3.85, 2.55, '재현성의 한계', '어떤 순서와 좌표로\n문제가 발생했는지 다시 찾기 어렵습니다.', C.blue);
    slide.addShape(pptx.ShapeType.roundRect, { x: 0.62, y: 5.35, w: 12.1, h: 1.05, rectRadius: 0.06, fill: { color: 'E8F7F1' }, line: { color: 'B9E9D9', width: 0.7 } });
    slide.addShape(pptx.ShapeType.ellipse, { x: 0.95, y: 5.64, w: 0.45, h: 0.45, fill: { color: C.teal }, line: { color: C.teal } });
    addText(slide, 'V', { x: 0.95, y: 5.73, w: 0.45, h: 0.15, fontSize: 11, bold: true, color: C.white, align: 'center' });
    addText(slide, '바이브 코딩 접근: 사용 흐름을 빠르게 구현하고, 실제 태블릿에서 보며 기능과 UX를 반복 검증', {
      x: 1.62, y: 5.62, w: 10.3, h: 0.26, fontSize: 15, bold: true, color: '146E55',
    });
    addFooter(slide, 2);
  }

  // 3. Architecture
  {
    const slide = pptx.addSlide();
    slide.background = { color: C.charcoal };
    addTitle(slide, '현재 솔루션은 “기기 관찰 + 입력 + 검증”을 한 화면에 모읍니다', 'ARCHITECTURE', true);
    const y = 2.05;
    const nodes = [
      { x: 0.78, label: '태블릿', sub: '실제 앱 화면', color: C.teal },
      { x: 3.52, label: 'ADB', sub: '캡처 · 입력', color: C.orange },
      { x: 6.26, label: 'Flask / Socket.IO', sub: '미러링 · 실행', color: C.blue },
      { x: 9.55, label: 'PC 브라우저', sub: '관찰 · 제어', color: C.teal },
    ];
    nodes.forEach((node, index) => {
      slide.addShape(pptx.ShapeType.roundRect, { x: node.x, y, w: 2.25, h: 1.36, rectRadius: 0.08, fill: { color: C.panel }, line: { color: C.line, width: 0.8 } });
      slide.addShape(pptx.ShapeType.ellipse, { x: node.x + 0.85, y: y - 0.28, w: 0.55, h: 0.55, fill: { color: node.color }, line: { color: node.color } });
      addText(slide, String(index + 1), { x: node.x + 0.85, y: y - 0.16, w: 0.55, h: 0.18, fontSize: 12, bold: true, color: C.white, align: 'center' });
      addText(slide, node.label, { x: node.x + 0.15, y: y + 0.38, w: 1.95, h: 0.22, fontSize: 15, bold: true, color: C.white, align: 'center' });
      addText(slide, node.sub, { x: node.x + 0.15, y: y + 0.79, w: 1.95, h: 0.16, fontSize: 10, color: 'B9C5D4', align: 'center' });
      if (index < nodes.length - 1) {
        addText(slide, '→', { x: node.x + 2.35, y: y + 0.57, w: 0.52, h: 0.28, fontSize: 24, bold: true, color: '74839A', align: 'center' });
      }
    });
    addText(slide, '보조 인식·실행 레이어', { x: 0.8, y: 4.3, w: 3.0, h: 0.25, fontSize: 13, bold: true, color: 'B9C5D4' });
    const layerCards = [
      ['UIAutomator XML', '텍스트 · bounds · 클릭 후보', C.teal],
      ['EasyOCR', '이미지·캔버스 텍스트 보조 인식', C.orange],
      ['Excel / Recorder', '시나리오 입력과 반복 재생', C.blue],
      ['AI Planner', '필요한 단계의 후보 선택 보조', 'A96DE3'],
    ];
    layerCards.forEach((item, index) => {
      const x = 0.8 + index * 3.0;
      slide.addShape(pptx.ShapeType.roundRect, { x, y: 4.72, w: 2.72, h: 1.25, rectRadius: 0.05, fill: { color: '1D2430' }, line: { color: C.line, width: 0.6 } });
      slide.addShape(pptx.ShapeType.rect, { x, y: 4.72, w: 0.06, h: 1.25, fill: { color: item[2] }, line: { color: item[2] } });
      addText(slide, item[0], { x: x + 0.22, y: 4.96, w: 2.25, h: 0.2, fontSize: 12, bold: true, color: C.white });
      addText(slide, item[1], { x: x + 0.22, y: 5.35, w: 2.26, h: 0.3, fontSize: 9.7, color: 'B9C5D4' });
    });
    addFooter(slide, 3, true);
  }

  // 4. Current features
  {
    const slide = pptx.addSlide();
    slide.background = { color: C.light };
    addTitle(slide, 'Work Area에서 실행·관찰·결과 확인을 분리했습니다', 'CURRENT PRODUCT');
    slide.addImage({ path: testCaseUi, x: 0.55, y: 1.52, w: 6.35, h: 3.575, line: { color: 'CDD6E0', width: 0.7 } });
    slide.addShape(pptx.ShapeType.roundRect, { x: 7.35, y: 1.55, w: 5.35, h: 4.42, rectRadius: 0.05, fill: { color: C.white }, line: { color: 'D8E0E9', width: 0.7 } });
    const bullets = [
      ['실시간 미러링', '태블릿 화면을 PC 브라우저에서 관찰하고 마우스 입력을 전달'],
      ['Excel Test Case', '업로드한 시나리오를 순서대로 실행하고 Pass / Fail / Block(N/A)로 분류'],
      ['AI TC RUN', '기존 XML·OCR 실행을 우선하고, 모호한 항목만 AI가 후보 기반으로 보조'],
      ['진행 가시화', '하단 Test Progress에서 실행 과정과 결과를 상태별로 확인'],
    ];
    bullets.forEach((item, index) => {
      const y2 = 1.86 + index * 0.94;
      slide.addShape(pptx.ShapeType.ellipse, { x: 7.68, y: y2, w: 0.3, h: 0.3, fill: { color: index === 0 ? C.teal : index === 1 ? C.blue : index === 2 ? 'A96DE3' : C.orange }, line: { color: 'FFFFFF', transparency: 100 } });
      addText(slide, item[0], { x: 8.18, y: y2 - 0.02, w: 3.9, h: 0.19, fontSize: 13.5, bold: true, color: C.text });
      addText(slide, item[1], { x: 8.18, y: y2 + 0.28, w: 3.9, h: 0.36, fontSize: 10.2, color: C.muted });
    });
    tag(slide, 0.7, 5.45, 2.62, '실제 로컬 솔루션 화면', C.blue);
    addFooter(slide, 4);
  }

  // 5. Repetition model
  {
    const slide = pptx.addSlide();
    slide.background = { color: C.charcoal };
    addTitle(slide, '초기 모델의 핵심은 “같은 테스트를 다시 돌릴 수 있는가”입니다', 'REPEATABLE QA', true);
    slide.addImage({ path: recorderUi, x: 0.62, y: 1.62, w: 5.6, h: 3.15, line: { color: C.line, width: 0.8 } });
    slide.addImage({ path: randomUi, x: 7.08, y: 1.62, w: 5.6, h: 3.15, line: { color: C.line, width: 0.8 } });
    const flows = [
      { x: 1.18, label: 'Record', color: C.red },
      { x: 2.63, label: 'Save', color: C.blue },
      { x: 4.08, label: 'Play 1~10회', color: C.teal },
      { x: 8.18, label: 'Memory', color: C.orange },
      { x: 9.78, label: 'UI 분석', color: C.blue },
      { x: 11.38, label: 'Live Run', color: C.teal },
    ];
    flows.forEach((item, index) => {
      const y = 5.36;
      slide.addShape(pptx.ShapeType.roundRect, { x: item.x, y, w: index === 2 ? 1.55 : 1.18, h: 0.54, rectRadius: 0.04, fill: { color: item.color }, line: { color: item.color } });
      addText(slide, item.label, { x: item.x + 0.08, y: y + 0.17, w: index === 2 ? 1.39 : 1.02, h: 0.16, fontSize: 9.5, bold: true, color: C.white, align: 'center' });
    });
    addText(slide, '→', { x: 2.3, y: 5.47, w: 0.25, h: 0.17, fontSize: 13, bold: true, color: '8090A5', align: 'center' });
    addText(slide, '→', { x: 3.83, y: 5.47, w: 0.25, h: 0.17, fontSize: 13, bold: true, color: '8090A5', align: 'center' });
    addText(slide, '→', { x: 9.45, y: 5.47, w: 0.25, h: 0.17, fontSize: 13, bold: true, color: '8090A5', align: 'center' });
    addText(slide, '→', { x: 11.05, y: 5.47, w: 0.25, h: 0.17, fontSize: 13, bold: true, color: '8090A5', align: 'center' });
    addText(slide, 'Recorder는 정해진 회귀 동작을, Random Test는 탐색형 동작을 반복 가능하게 만듭니다.', {
      x: 0.72, y: 6.25, w: 11.5, h: 0.24, fontSize: 14, color: 'C9D2DE',
    });
    addFooter(slide, 5, true);
  }

  // 6. AI expansion
  {
    const slide = pptx.addSlide();
    slide.background = { color: C.light };
    addTitle(slide, 'AI는 막히는 지점의 보조 장치입니다', 'AI AUGMENTATION');
    const steps = [
      ['1', '기존 엔진 우선', 'UIAutomator XML과 OCR로\n텍스트·좌표 후보를 먼저 찾습니다.', C.teal],
      ['2', 'AI 보조 분석', '모호하거나 실패한 항목만\n현재 화면과 후보 목록을 분석합니다.', 'A96DE3'],
      ['3', '후보 기반 실행', 'AI는 새 좌표를 만들지 않고\n현재 후보 중 하나만 선택합니다.', C.blue],
      ['4', '결과 누적', 'ai-assisted 결과와 이전 이력을\n다음 실행의 참고 정보로 남깁니다.', C.orange],
    ];
    steps.forEach((item, index) => {
      const x = 0.7 + index * 3.12;
      slide.addShape(pptx.ShapeType.roundRect, { x, y: 2.0, w: 2.72, h: 2.7, rectRadius: 0.07, fill: { color: C.white }, line: { color: 'D7E0EA', width: 0.7 } });
      slide.addShape(pptx.ShapeType.ellipse, { x: x + 0.98, y: 1.68, w: 0.76, h: 0.76, fill: { color: item[3] }, line: { color: item[3] } });
      addText(slide, item[0], { x: x + 0.98, y: 1.9, w: 0.76, h: 0.2, fontSize: 16, bold: true, color: C.white, align: 'center' });
      addText(slide, item[1], { x: x + 0.2, y: 2.65, w: 2.32, h: 0.22, fontSize: 14, bold: true, color: C.text, align: 'center' });
      addText(slide, item[2], { x: x + 0.23, y: 3.15, w: 2.25, h: 0.65, fontSize: 10.6, color: C.muted, align: 'center', valign: 'mid' });
      if (index < steps.length - 1) {
        addText(slide, '→', { x: x + 2.78, y: 3.13, w: 0.3, h: 0.2, fontSize: 18, bold: true, color: '91A0B2', align: 'center' });
      }
    });
    slide.addShape(pptx.ShapeType.roundRect, { x: 1.2, y: 5.45, w: 10.9, h: 0.75, rectRadius: 0.05, fill: { color: 'EBF2FF' }, line: { color: 'BED0F4', width: 0.6 } });
    addText(slide, 'AI의 장점: 화면 변화·텍스트·이전 실패 맥락을 함께 보고, 사람이 작성하기 어려운 탐색 우선순위를 제안할 수 있습니다.', {
      x: 1.55, y: 5.68, w: 10.2, h: 0.2, fontSize: 13.4, bold: true, color: '3056A8', align: 'center',
    });
    addFooter(slide, 6);
  }

  // 7. Governance and cost
  {
    const slide = pptx.addSlide();
    slide.background = { color: C.charcoal };
    addTitle(slide, '권장은 “기본 자동화는 고정, AI는 선택 적용”입니다', 'OPERATING MODEL', true);
    slide.addShape(pptx.ShapeType.roundRect, { x: 0.72, y: 1.72, w: 5.72, h: 4.45, rectRadius: 0.06, fill: { color: '1D2430' }, line: { color: C.line, width: 0.7 } });
    addText(slide, '기본 반복 테스트', { x: 1.05, y: 2.05, w: 2.5, h: 0.25, fontSize: 19, bold: true, color: C.teal });
    addText(slide, '비용 없이 자주 수행', { x: 1.05, y: 2.43, w: 2.5, h: 0.2, fontSize: 11, color: 'B9C5D4' });
    ['Recorder: 핵심 회귀 동작 반복', 'Excel: 정의된 Test Case 순차 실행', 'Live Run: UI 후보 기반 탐색'].forEach((item, index) => {
      const y = 3.04 + index * 0.72;
      slide.addShape(pptx.ShapeType.ellipse, { x: 1.05, y, w: 0.28, h: 0.28, fill: { color: C.teal }, line: { color: C.teal } });
      addText(slide, item, { x: 1.55, y: y + 0.01, w: 4.22, h: 0.18, fontSize: 12.5, color: C.white });
    });
    slide.addShape(pptx.ShapeType.roundRect, { x: 6.88, y: 1.72, w: 5.72, h: 4.45, rectRadius: 0.06, fill: { color: '25223A' }, line: { color: '514D74', width: 0.7 } });
    addText(slide, 'AI 선택 적용', { x: 7.22, y: 2.05, w: 2.5, h: 0.25, fontSize: 19, bold: true, color: 'C9A5F0' });
    addText(slide, 'API 비용이 발생하는 확장 영역', { x: 7.22, y: 2.43, w: 3.5, h: 0.2, fontSize: 11, color: 'C9C2D8' });
    ['AI Random Run: 탐색 순서 보조', 'AI TC RUN: 모호한 Test Case 복구', 'AI 예산 상한: 실행별 사용량 관리'].forEach((item, index) => {
      const y = 3.04 + index * 0.72;
      slide.addShape(pptx.ShapeType.ellipse, { x: 7.22, y, w: 0.28, h: 0.28, fill: { color: 'A96DE3' }, line: { color: 'A96DE3' } });
      addText(slide, item, { x: 7.72, y: y + 0.01, w: 4.22, h: 0.18, fontSize: 12.5, color: C.white });
    });
    slide.addShape(pptx.ShapeType.roundRect, { x: 1.34, y: 6.42, w: 10.65, h: 0.45, rectRadius: 0.04, fill: { color: '352A2E' }, line: { color: '6A474D', width: 0.6 } });
    addText(slide, '안전 원칙: 결제 · 구매 · 탈퇴 · 삭제 · 초기화 · 시스템 설정 변경은 자동 실행 대상에서 차단', { x: 1.55, y: 6.55, w: 10.2, h: 0.14, fontSize: 10.8, bold: true, color: 'F2C2CA', align: 'center' });
    addFooter(slide, 7, true);
  }

  // 8. Conclusion and roadmap
  {
    const slide = pptx.addSlide();
    slide.background = { color: C.light };
    addTitle(slide, '다음 단계는 “더 많이 자동화”보다 “더 신뢰성 있게 자동화”입니다', 'RECOMMENDATION');
    const timeline = [
      { n: 1, x: 1.2, title: '지금', body: '실제 기기 미러링과\n반복 QA가 가능한 프로토타입', color: C.teal },
      { n: 2, x: 4.25, title: '다음', body: 'AI 예산 관리 · 재계획 ·\n결과 리포트 고도화', color: C.blue },
      { n: 3, x: 7.3, title: '확장', body: 'Appium 기반 구조화 자동화와\n핵심 시나리오 회귀 체계화', color: C.orange },
      { n: 4, x: 10.35, title: '권장', body: '핵심은 고정 테스트,\n탐색과 예외는 AI에 위임', color: 'A96DE3' },
    ];
    slide.addShape(pptx.ShapeType.line, { x: 1.45, y: 3.15, w: 10.4, h: 0, line: { color: 'C7D1DC', width: 1.3 } });
    timeline.forEach((item) => {
      slide.addShape(pptx.ShapeType.ellipse, { x: item.x, y: 2.75, w: 0.78, h: 0.78, fill: { color: item.color }, line: { color: item.color } });
      addText(slide, String(item.n), { x: item.x, y: 2.98, w: 0.78, h: 0.18, fontSize: 16, bold: true, color: C.white, align: 'center' });
      addText(slide, item.title, { x: item.x - 0.52, y: 3.87, w: 1.83, h: 0.24, fontSize: 16, bold: true, color: C.text, align: 'center' });
      addText(slide, item.body, { x: item.x - 0.85, y: 4.35, w: 2.5, h: 0.62, fontSize: 10.5, color: C.muted, align: 'center', valign: 'mid' });
    });
    slide.addShape(pptx.ShapeType.roundRect, { x: 1.25, y: 5.86, w: 10.85, h: 0.76, rectRadius: 0.05, fill: { color: C.charcoal }, line: { color: C.charcoal } });
    addText(slide, '권장 결론  |  반복 검증은 Recorder · Excel로 표준화하고, 탐색 · 화면 인식 · 예외 판단에 AI를 선택적으로 사용합니다.', {
      x: 1.55, y: 6.09, w: 10.25, h: 0.2, fontSize: 13.2, bold: true, color: C.white, align: 'center',
    });
    addFooter(slide, 8);
  }

  await pptx.writeFile({ fileName: OUTPUT });
  console.log(`Created ${OUTPUT}`);
}

build().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
