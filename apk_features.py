"""Local, evidence-bound checks from compiled Android UI resources."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import zipfile

from app_analysis import AnalysisError, digest

POLICY = 'apk-ui-v2'
LIBRARY = re.compile(r'^(?:abc_|design_|support_|notification_|select_dialog_|mtrl_|material_|m3_|exo_|ime_.*split_test|browser_actions_|custom_dialog$|test_action_)')
RESOURCE = re.compile(r'^\s*resource (0x[0-9a-f]+) (\w+)/([\w.]+)')
VALUE = re.compile(r'^\s*\(([^)]*)\) (.*)')
ELEMENT = re.compile(r'^(\s*)E: ([^ ]+)')
ATTR = re.compile(r'^\s*A: http://schemas.android.com/apk/res/android:([\w]+)\([^)]*\)=(.*)')


def aapt():
    binary = 'aapt2.exe' if os.name == 'nt' else 'aapt2'
    for root in (os.environ.get('ANDROID_HOME'), os.environ.get('ANDROID_SDK_ROOT'),
                 Path(os.environ.get('LOCALAPPDATA', ''))/'Android'/'Sdk'):
        if root:
            choices = [p for p in (Path(root)/'build-tools').glob('*/'+binary)
                       if re.fullmatch(r'\d+(?:\.\d+)+', p.parent.name)]
            if choices:
                return max(choices,key=lambda p:tuple(map(int,p.parent.name.split('.'))))
    raise AnalysisError('분석 오류: Android SDK Build Tools의 aapt2를 찾을 수 없습니다.',503)


def dump(tool, path, mode, filename=None, timeout=20):
    args = [str(tool),'dump',mode,str(path)] + (['--file',filename] if filename else [])
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as proc:
        timer = threading.Timer(timeout,proc.kill)
        timer.start()
        try:
            output = proc.stdout.read(16*1024*1024+1)
            if len(output)>16*1024*1024:
                proc.kill()
                raise AnalysisError('분석 오류: 리소스 출력의 안전 한도를 초과했습니다.',422)
            if proc.wait():
                raise AnalysisError('분석 오류: aapt2가 '+(filename or '리소스 표')+'를 읽지 못했습니다.',422)
            return output.decode('utf-8')
        finally:
            timer.cancel()


def literal(value):
    try:
        return json.JSONDecoder().raw_decode(value)[0] if value.startswith('"') else ''
    except ValueError:
        return ''


def table(text, package):
    result, current, owner = {}, None, None
    for line in text.splitlines():
        if line.startswith('Package name='):
            owner = line.split('=',1)[1].split()[0]
        header = RESOURCE.match(line)
        if header:
            rid,kind,name = header.groups()
            current = None
            if owner == package and kind in {'layout','menu','string','id'}:
                current = result[rid] = {'name':name,'kind':kind,'values':[]}
        elif current and (m:=VALUE.match(line)):
            current['values'].append(m.groups())
    return result


def nodes(tree):
    """Retain nesting so text in statically hidden subtrees isn't promoted."""
    stack = []
    for line in tree.splitlines():
        if m:=ELEMENT.match(line):
            indent,tag = m.groups()
            while stack and stack[-1][0]>=len(indent):
                yield stack.pop()[1]
            hidden = bool(stack and stack[-1][1]['hidden'])
            stack.append((len(indent),{'tag':tag,'hidden':hidden}))
        elif stack and (m:=ATTR.match(line)):
            key,value=m.groups()
            stack[-1][1][key]=value
            if key=='visibility' and value not in ('0','0x00000000'):
                stack[-1][1]['hidden']=True
    while stack:
        yield stack.pop()[1]


def extract(path, sha, result, phase, cancelled):
    if digest(path)!=sha:
        raise AnalysisError('분석 오류: 등록 APK 해시가 일치하지 않습니다.',409)
    deadline=time.monotonic()+180
    with zipfile.ZipFile(path) as archive:
        has_table='resources.arsc' in archive.namelist()
    tool=aapt() if has_table else None
    text=dump(tool,path,'resources') if has_table else ''
    if has_table and not re.search(r'^Package name='+re.escape(result['package_name'])+r'\s',text,re.M):
        raise AnalysisError('분석 오류: 대상 패키지의 리소스 표를 해석할 수 없습니다.',422)
    resources=table(text,result['package_name'])
    phase('기능 후보 분류 중')
    found, pending, excluded, used = {}, set(), Counter(), set()
    for r in resources.values():
        if r['kind'] not in ('layout','menu'):
            continue
        name=r['kind']+'/'+r['name']
        if LIBRARY.match(r['name']):
            excluded['공용 라이브러리 리소스']+=1
            continue
        seen_paths=set()
        for config,value in r['values']:
            file=re.match(r'\(file\) (res/[^\s]+\.xml)(?:\s|$)',value)
            if not file or file[1] in seen_paths:
                continue
            seen_paths.add(file[1])
            if cancelled():
                raise InterruptedError()
            if time.monotonic()>deadline:
                raise AnalysisError('분석 오류: 전체 리소스 분석 제한 시간을 초과했습니다.',422)
            tree=dump(tool,path,'xmltree',file[1])
            for node in nodes(tree):
                tag=node['tag'].split('.')[-1]
                if node['hidden']:
                    excluded['정적으로 숨겨진 요소']+=1
                    continue
                vid=resources.get(node.get('id','').lstrip('@'),{}).get('name','')
                if tag in ('WebView','ComposeView'):
                    pending.add(name+' / '+tag+' · 동적 화면')
                key=next((k for k in ('title','text','contentDescription') if k in node),None)
                if not key:
                    if (tag.endswith(('Button','Switch','CheckBox')) or node.get('clickable') in ('true','0xffffffff')) and vid:
                        pending.add(name+' / '+vid+' · 표시 이름 미확인')
                    continue
                raw=node[key]; resource=resources.get(raw.lstrip('@'),{})
                if resource.get('kind')=='string':
                    used.add(raw.lstrip('@'))
                    options=dict(resource['values'])
                    label=literal(options.get('ko') or options.get('') or '')
                else:
                    label=literal(raw)
                if not label or len(label)>180 or not re.search(r'[^\W\d_]',label) or re.search(r'%[\d$.]*[sdf]|[\r\n]',label):
                    excluded['빈 값·서식·동적 문구']+=1
                    continue
                # A label is evidence of presentation, never of a destination or side effect.
                signature=(name,vid or label,key)
                if signature in found:
                    excluded['동일 요소의 변형']+=1
                    continue
                found[signature]={'screen':name,'label':label,'view':vid or tag,'attribute':key,
                                  'locator':name+' / '+file[1]+' / '+(vid or tag)+' / '+key,
                                  'configuration':config or 'default'}
    excluded['UI 요소와 연결되지 않은 문자열']=sum(r['kind']=='string' and rid not in used for rid,r in resources.items())
    for c in result.get('components',[]):
        if c.get('type') in ('activity','activity-alias') and not c['name'].startswith(('androidx.','com.google.android.')):
            pending.add(c['name']+' · 화면 선언과 사용자 진입 경로의 연결 미확인')
    return list(found.values()),sorted(pending),dict(excluded)
