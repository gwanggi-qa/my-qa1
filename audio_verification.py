"""Conservative text/audio comparison; never authorizes issue publication."""
import re
import unicodedata

_JA_DIGITS = {'〇': 0, '零': 0, '一': 1, '二': 2, '三': 3, '四': 4,
              '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
_JA_UNITS = {'十': 10, '百': 100, '千': 1000}


def _japanese_number(match):
    value = match.group(0)
    if not any(c in _JA_UNITS for c in value):
        return ''.join(str(_JA_DIGITS[c]) for c in value)
    total, digit = 0, 0
    for char in value:
        if char in _JA_DIGITS:
            digit = _JA_DIGITS[char]
        else:
            total += (digit or 1) * _JA_UNITS[char]
            digit = 0
    return str(total + digit)


def normalize(text, language='ja'):
    text = unicodedata.normalize('NFKC', text or '')
    if language == 'en':
        text = text.casefold()
    if language == 'ja':
        text = re.sub(r'[〇零一二三四五六七八九十百千]+', _japanese_number, text)
        text = ''.join(chr(ord(c) - 0x60) if '\u30a1' <= c <= '\u30f6' else c for c in text)
    return ''.join(c for c in text if not c.isspace()
                   and not unicodedata.category(c).startswith('P'))


def classify(expected, transcript, *, duration=None, audio_available=True, language='ja'):
    """Return evidence status, not a claim that device output was heard."""
    if not audio_available or not expected or not transcript:
        return {'status': 'unavailable', 'reason': 'Missing text, transcript or audio'}
    if language == 'zh' and re.search(r'[a-zA-Z\u00c0-\u024f]', expected):
        return {'status': 'review', 'reason': 'Spelling or speech recognition ambiguity'}
    left, right = normalize(expected,language), normalize(transcript,language)
    if duration is not None and duration < 2:
        return {'status': 'review', 'reason': 'Short utterance requires listening review'}
    if left == right:
        return {'status': 'match', 'reason': 'Normalized transcript matches display text'}
    left_numbers, right_numbers = re.findall(r'\d+', left), re.findall(r'\d+', right)
    if left_numbers and right_numbers and ''.join(left_numbers) != ''.join(right_numbers):
        return {'status': 'mismatch_candidate', 'reason': 'Numeric content differs; confirm by listening'}
    if language == 'ja' and ('か' == right[-1:]) != ('か' == left[-1:]):
        return {'status': 'mismatch_candidate', 'reason': 'Question/statement differs; confirm by listening'}
    return {'status': 'review', 'reason': 'Spelling or speech recognition ambiguity'}
