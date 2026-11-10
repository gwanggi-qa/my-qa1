"""Observed, read-only display routes for the Japanese Premium Android app."""
import json
import re
import xml.etree.ElementTree as ET
from execution_results import now

PACKAGE='com.weaversmind.tjp2'
SHA='e16b35f84e19ccb670404e7e3b43f712a7048708377ca7b4393fb41d06c841bc'
def installed_build(m):
    match=re.search(r'\bversionCode=(\d+)\b',m.adb_text('shell','dumpsys','package',PACKAGE))
    return match[1] if match else '확인 불가'

def scope(build):
    return f'TC 생성 빌드 6 / 승인 대상 실기기 빌드 {build} · 화면 표시만 검증, 빌드 6 미검증 · 확인된 메뉴 경로 및 뒤로가기 최대 5회'
# Require independent landmarks; do not use the asserted value to establish arrival.
SCREENS={
    'menu_main':('MainActivity',('cvMainStudy','cvReport','cvSetting')),
    'menu_schedule_new':('MenuScheduleNewActivity',('cvLeftArrow','btnTotalSchedule','llEpisodeRow')),
    'menu_management':('MenuReportActivity',('cvCalendar','cvProgress','cvScore')),
    'menu_note':('MenuNoteActivity',('tvSortDropdown','tvFilterDropdown','btnAddStudy')),
    'menu_setting':('MenuSettingActivity',('cvSettingStudy','cvSettingInfo','cvSettingSystem'))}
ENTRY={'menu_schedule_new':'cvMainStudy','menu_management':'cvReport','menu_note':'cvNote','menu_setting':'cvSetting'}
INFO={'menu_management':'숫자로 딱 정리된 리포트를 확인해보세요.',
      'menu_note':'표현별로 다시 말하며 내 것으로 만들어보세요!'}
UPDATE='업데이트할 데이터가 있습니다.\n업데이트 하시겠습니까?'
GROUP={('menu_main','학습 진행률'):'cvMainStudy',('menu_main','학습노트'):'cvNote',
       ('menu_main','학습리포트'):'cvReport',('menu_schedule_new','전체 커리큘럼'):'btnTotalSchedule'}


def bind(workbook, steps):
    if 'Sources' not in workbook.sheetnames:
        return steps
    rows=iter(workbook['Sources'].values)
    header=next(rows,())
    if tuple(header[:8])!=('Evidence ID','Reference ID','Location','Excerpt','Kind','Filename','Reference SHA256','Build SHA256'):
        return steps
    sources={}
    for row in rows:
        if row[7]!=SHA or row[4]!='implementation_only' or not isinstance(row[3],str):
            continue
        try: item=json.loads(row[3])
        except ValueError: continue
        if isinstance(item,dict) and item.get('locator')==row[2]:
            sources[row[0]]=item
    for step in steps:
        ref=re.search(r'근거: (E\d+)\s*$',step.get('note',''))
        item=sources.get(ref[1]) if ref else None
        if not item or item.get('attribute')!='text': continue
        screen,view,label=(item.get(k,'') for k in ('screen','view','label'))
        if screen not in {'layout/'+k for k in SCREENS} or not view or not label: continue
        if (step.get('test_item')=='1. '+view+' 요소의 문구를 확인한다.' and
            step.get('expected')==view+'의 문구가 「'+label+'」이다.' and
            step.get('precondition')=='등록 빌드에서 '+screen+' 화면 또는 영역이 열려 있고 '+view+'가 표시되는 상태이다.'):
            step['_display_spec']={'screen':screen.split('/')[1],'view':view,'label':label}
    return steps


def elements(root):
    return [n for n in root.iter('node') if n.get('package')==PACKAGE]


def rid(n):
    return n.get('resource-id','').removeprefix(PACKAGE+':id/')


def recognize(root,activity):
    ids={rid(n) for n in elements(root)}
    for name,(cls,anchors) in SCREENS.items():
        if cls==activity and set(anchors)<=ids:return name
    return None


def route(root,activity,goal):
    nodes=elements(root)
    ids={rid(n):n.get('text','') for n in nodes}
    if 'popup_content' in ids:
        if activity not in ('MainActivity',SCREENS[goal][0]):raise ValueError('예상 화면 밖의 팝업입니다.')
        if goal in INFO and ids['popup_content']==INFO[goal] and ids.get('btn_sel1')=='확인':
            return 'tap','btn_sel1'
        if goal=='menu_setting' and ids['popup_content']==UPDATE and ids.get('btn_sel2')=='취소':
            return 'tap','btn_sel2'
        raise ValueError('알 수 없는 팝업: 확인·취소 입력을 보내지 않았습니다.')
    here=recognize(root,activity)
    if not here:raise ValueError('현재 Activity와 화면 요소를 확인할 수 없습니다: '+activity)
    if here==goal:return 'arrived',here
    if here=='menu_main':return 'tap',ENTRY[goal]
    return 'back',here


def observe(root,spec):
    view,label,screen=(spec[k] for k in ('view','label','screen'))
    nodes=elements(root)
    if view!='TextView':
        nodes=[n for n in nodes if rid(n)==view]
    else:
        group=GROUP.get((screen,label))
        if group:
            parents=[n for n in nodes if rid(n)==group]
            if len(parents)!=1:return None
            nodes=elements(parents[0])
        nodes=[n for n in nodes if n.get('class')=='android.widget.TextView']
        if screen=='menu_setting' and label=='환경설정':
            # The title is a direct child of the settings root, outside the menu cards.
            nodes=[n for p in root.iter('node') if any(rid(k)=='cvSettingStudy' for k in p.iter('node'))
                   for n in p.findall('node') if n in nodes and not n.get('resource-id')]
        elif len(nodes)!=1:
            nodes=[n for n in nodes if n.get('text')==label]
    return nodes[0].get('text') if len(nodes)==1 else None


def activity(m):
    s=m.adb_text('shell','dumpsys','activity','activities')
    match=re.search(r'(?:topResumedActivity|mResumedActivity)[^\n]*?\s([\w.]+)/([\w.]+)',s)
    if not match or match[1]!=PACKAGE:
        raise m.BlockedStep('전면 앱 불일치: 기대 '+PACKAGE+' / 실제 '+(match[1] if match else '확인 불가'))
    return match[2].split('.')[-1]


def snapshot(w):
    w.guard()
    cls=activity(w.host)
    try: root=ET.fromstring(w.host.dump_ui_xml())
    except ET.ParseError as exc:raise w.host.BlockedStep('UI XML을 읽을 수 없습니다.') from exc
    if activity(w.host)!=cls:raise w.host.BlockedStep('관찰 도중 Activity가 전환되었습니다.')
    return root,cls


def navigate(w,step,case):
    m=w.host; goal=step['_display_spec']['screen']
    planned=step.get('_display_build')
    actual=installed_build(m)
    if planned not in ('7','8') or actual!=planned:
        raise m.BlockedStep(f'설치 빌드 불일치: 승인 {planned} / 실제 {actual}. 화면 문구 미검증.')
    visited=set()
    while True:
        root,cls=snapshot(w)
        action,target=route(root,cls,goal)
        if action=='arrived':
            return f'{PACKAGE}/{cls} · {goal} 화면 요소 확인', [w.evidence('목표 화면 도착')]
        signature=(cls,action,target)
        if signature in visited or len(visited)>=5:
            raise m.BlockedStep('동일 화면 재시도 또는 TC당 5회 이동 한도로 중단')
        visited.add(signature)
        entry={'number':len(case['stages']),'source':goal,'action':action,'target':target,'supplemental':True,
               'actual':f'{cls} → {action} {target}','status':'running','evidence':[w.evidence('이동 전')],
               'preconditions_verified':True,'expectations_verified':False}
        case['stages'].append(entry)
        with m._adb_input_lock:
            if installed_build(m)!=planned:raise m.BlockedStep('입력 전 설치 빌드가 변경되었습니다.')
            root,fresh=snapshot(w)
            if fresh!=cls or route(root,fresh,goal)!=(action,target):raise m.BlockedStep('입력 전 화면이 변경됐습니다.')
            args=('keyevent','4')
            if action=='tap':
                nodes=[n for n in elements(root) if rid(n)==target and n.get('enabled')=='true' and n.get('clickable')=='true']
                center=m.bounds_center(nodes[0].get('bounds')) if len(nodes)==1 else None
                if not center:raise m.BlockedStep('현재 클릭 대상이 유일하지 않습니다: '+target)
                args=('tap',str(center['x']),str(center['y']))
            w.guard();w.phase('실제 조작 중 · '+entry['actual'],'device_input')
            _,_,rc=m.adb('shell','input',*args,timeout_s=8)
            if rc:raise RuntimeError('ADB input outcome uncertain')
            step.setdefault('_input_events',[]).append({'action':action,'target':target,'status':'adb_accepted','at':now()})
        if w.cancel.wait(.5):w.guard()
        entry['status']='executed';entry['evidence'].append(w.evidence('이동 후'))


def check(w,step):
    spec=step['_display_spec']; root,cls=snapshot(w)
    if installed_build(w.host)!=step.get('_display_build'):raise w.host.BlockedStep('검증 전 설치 빌드가 변경되었습니다.')
    if route(root,cls,spec['screen'])[0]!='arrived':raise w.host.BlockedStep('검사 직전 목표 화면이 변경됐습니다.')
    value=observe(root,spec)
    if value is None:return None
    msg=f"{cls} · {spec['view']} · 기대 「{spec['label']}」 / 실제 「{value}」"
    step['_display_actual']=msg
    evidence=w.evidence('대상 문구 XML 검사')
    evidence.update(method='ui-assertion',actual=value,expected=spec['label'],activity=cls,view=spec['view'])
    if value!=spec['label']:
        if spec['view']=='tvLevelTitle':msg+=' · 현재 선택 레벨 차이, 제품 결함 여부 미확정'
        step['_display_actual']=msg
        case=w.data['results'][step['_workflow_index']-1]
        case['stages'][-1]['evidence'].append(evidence)
        raise AssertionError(msg)
    return msg,[evidence]
