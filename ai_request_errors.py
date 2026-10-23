"""Safe provider failures; never expose provider logs or request contents."""

DRAFT_TIMEOUT_S = 600
ERRORS = {
    'timeout': ('시간 초과', '제한 시간 내 응답을 받지 못했습니다. 생성 개수나 참고자료 범위를 줄인 뒤 다시 시도하세요.'),
    'authentication': ('인증 필요', '서버의 AI 로그인 또는 인증 설정을 확인하세요. Codex는 codex login으로 다시 로그인하세요.'),
    'usage_limit': ('사용량 한도', '계정 한도와 재설정 시각을 확인한 뒤 다시 시도하세요.'),
    'connection': ('연결 오류', '서버 PC의 네트워크와 AI 서비스 연결 상태를 확인하세요.'),
    'invalid_response': ('응답 형식 오류', '응답 JSON 또는 근거 ID가 유효하지 않습니다. 자료 범위를 확인한 뒤 다시 시도하세요.'),
    'cli_execution': ('CLI 실행 오류', 'Codex 설치 경로와 버전, 실행 권한을 확인하세요.'),
    'unknown': ('기타 오류', '원인을 확정하지 못했습니다. 서버 설정을 점검하세요. 자동 재시도는 하지 않았습니다.'),
}


class AIRequestError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in ERRORS else 'unknown'
        title, hint = ERRORS[self.code]
        super().__init__(title + ': ' + hint)


def cli_error_code(diagnostic):
    text = diagnostic.casefold()
    if any(s in text for s in ('usage limit', 'rate limit', 'quota', 'usage_limit', '429')):
        return 'usage_limit'
    if any(s in text for s in ('unauthorized', 'not logged', '401', 'refresh token')):
        return 'authentication'
    if any(s in text for s in ('connection refused', 'connection reset', 'dns error',
                              'error sending request', 'failed to connect', 'network is unreachable')):
        return 'connection'
    return 'cli_execution'
