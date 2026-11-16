"""Read-only installed course inventory; listing an app does not enable injection."""
import re

SUBJECTS = (
    ('japanese-premium', '더위크 일본어 프리미엄', 'com.weaversmind.tjp2'),
    ('japanese', '더위크 일본어', 'com.weaversmind.tjp'),
    ('chinese', '더위크 중국어', 'com.weaversmind.tcn'),
    ('travel-english', '더위크 여행영어', 'com.weaversmind.trip'),
    ('toeic', '뇌새김 TOEIC 완성', 'com.weaversmind.toeic'),
)


def inventory(serial, adb):
    from speech_word import APK_SHA256
    if adb(['get-state']).strip() != b'device':
        raise ValueError('사용 가능한 기기를 선택하세요.')
    raw = adb(['shell', 'pm', 'list', 'packages', 'com.weaversmind']).decode('utf-8', 'replace')
    installed = {line[8:].strip() for line in raw.splitlines() if line.startswith('package:')}
    items = []
    for subject, name, package in SUBJECTS:
        if package not in installed:
            continue
        entry = dict(id=subject, name=name, package=package, build=None, supported=False,
                     support_status='not_integrated', reason='음성 인식 검증 연동이 필요합니다.')
        try:
            dump = adb(['shell', 'dumpsys', 'package', package]).decode('utf-8', 'replace')
            codes = set(re.findall(r'\bversionCode=(\d+)\b', dump))
            entry['build'] = int(next(iter(codes))) if len(codes) == 1 else None
            if subject == 'japanese-premium':
                entry.update(support_status='unsupported_build', reason='현재 검증된 빌드 11만 지원합니다.')
                if entry['build'] == 11:
                    paths = adb(['shell', 'pm', 'path', package]).decode().splitlines()
                    if len(paths) != 1 or not paths[0].startswith('package:'):
                        raise ValueError('APK 경로 확인 불가')
                    path = paths[0][8:]
                    if not re.fullmatch(r'/data/app/[-A-Za-z0-9_./=+~]+/base\.apk', path) or '..' in path.split('/'):
                        raise ValueError('APK 경로 확인 불가')
                    digest = adb(['shell', 'sha256sum', path]).decode().split()
                    if len(digest) != 2 or digest[1] != path or digest[0].lower() != APK_SHA256:
                        entry.update(reason='설치 APK가 검증된 파일과 다릅니다. 추가 연동 확인이 필요합니다.')
                    else:
                        entry.update(supported=True, support_status='supported',
                                     reason='지원 가능 · DB 조회 및 실행 전에 해당 앱을 열어 주세요. 실행 시 연결 상태를 다시 확인합니다.')
        except (ValueError, OSError, RuntimeError, TimeoutError):
            entry.update(support_status='unavailable', reason='설치 버전 또는 APK를 확인하지 못했습니다. 다시 조회해 주세요.')
        entry['capabilities'] = dict.fromkeys(('catalog', 'native', 'input', 'verdict'), entry['supported'])
        items.append(entry)
    return dict(device=serial, items=items)
