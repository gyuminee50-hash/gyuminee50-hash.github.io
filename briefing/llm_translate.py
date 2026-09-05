"""
Groq (Llama 3) 기반 번역·정제 모듈
완전 무료, 결제 불필요, 하드 한도 (청구 없음)
실패 시 Google Translate fallback
"""
import re, json, os, requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    _cfg = json.load(f)

_API_KEY = _cfg.get('groq_api_key', '')
_URL     = 'https://api.groq.com/openai/v1/chat/completions'
_MODEL   = 'qwen/qwen3.8-27b'


def _call(prompt):
    """Groq API 단일 호출"""
    resp = requests.post(
        _URL,
        headers={'Authorization': f'Bearer {_API_KEY}', 'Content-Type': 'application/json'},
        json={'model': _MODEL,
              'messages': [{'role': 'user', 'content': prompt}],
              'temperature': 0.3, 'max_tokens': 600},
        timeout=30
    )
    data = resp.json()
    if 'error' in data:
        raise Exception(data['error'].get('message', str(data['error'])))
    return data['choices'][0]['message']['content'].strip()


def _parse_numbered(text, expected=None):
    """'1. xxx\\n2. xxx' 형식 파싱. expected=None 이면 개수 체크 안 함"""
    parsed = []
    for line in text.splitlines():
        m = re.match(r'^\d+[.)]\s*(.+)', line.strip())
        if m:
            parsed.append(m.group(1).strip())
    if expected is not None and len(parsed) != expected:
        return None
    return parsed


# ── 영어 제목 리스트 → 한국어 헤드라인 리스트 ────────────────────
_EN_TITLES_PROMPT = """\
아래 영어 금융·경제 뉴스 제목들을 자연스러운 한국어 신문 헤드라인으로 번역해줘.
번호와 함께 한 줄씩 출력. 설명·부연 없이 결과만.

규칙:
- 30자 이내, 짧고 명확하게
- ~했다 / ~된다 / 명사형 마무리
- Fed→연준, Nasdaq→나스닥, 기업명은 그대로
- 숫자·%·$ 그대로 유지
- 한자(漢字) 절대 사용 금지 — 순한글 또는 아라비아 숫자만
- 금융 용어 예시: trillionaire→"1조달러 자산가", beat earnings→"실적 상회", rate cut→"금리 인하"
- 뜻이 분명하게 전달되도록 — 독자가 제목만 봐도 내용을 이해할 수 있어야 함

{items}"""


def translate_titles(en_titles):
    """영어 제목 리스트 → 한국어 헤드라인 리스트 (배치)"""
    if not en_titles:
        return []
    if not _API_KEY:
        return _fallback_translate(en_titles)
    try:
        numbered = '\n'.join(f"{i+1}. {t}" for i, t in enumerate(en_titles))
        raw = _call(_EN_TITLES_PROMPT.format(items=numbered))
        parsed = _parse_numbered(raw, len(en_titles))
        return parsed if parsed else _fallback_translate(en_titles)
    except Exception as e:
        print(f'  [Groq 오류] {e} → fallback')
        return _fallback_translate(en_titles)


def translate_title(en_title):
    r = translate_titles([en_title])
    return r[0] if r else en_title


# ── 한국어 제목 리스트 → 헤드라인 스타일 정제 ────────────────────
_KO_TITLES_PROMPT = """\
아래 한국어 뉴스 제목들을 신문 헤드라인 스타일로 다듬어줘.
번호와 함께 한 줄씩 출력. 설명 없이 결과만.

규칙:
- 30자 이내, 간결하게
- 합니다체 → 한다체 (습니다→다, 합니다→한다, 됩니다→된다)
- 불필요한 조사·어미·인용구 제거

{items}"""


def refine_ko_titles(ko_titles):
    """한국어 제목 리스트 → 헤드라인 스타일 정제 (배치)"""
    if not ko_titles:
        return []
    if not _API_KEY:
        return ko_titles
    try:
        numbered = '\n'.join(f"{i+1}. {t}" for i, t in enumerate(ko_titles))
        raw = _call(_KO_TITLES_PROMPT.format(items=numbered))
        parsed = _parse_numbered(raw, len(ko_titles))
        return parsed if parsed else ko_titles
    except Exception as e:
        print(f'  [Groq 한글 제목 오류] {e}')
        return ko_titles


def refine_ko_title(ko_title):
    r = refine_ko_titles([ko_title])
    return r[0] if r else ko_title


# ── 영어 문장들 → 한국어 불릿 ────────────────────────────────────
_US_BULLETS_PROMPT = """\
아래 영어 금융·경제 뉴스 문장들에서 핵심 팩트를 추려 한국어 불릿으로 만들어줘.
최대 {max}개, 번호 형식으로 출력. 설명 없이 결과만.

규칙:
- 45자 이내, 숫자만 나열 금지 — 반드시 "주어+내용" 구조로
- 한자(漢字) 절대 사용 금지
- 숫자·%·$ 그대로 유지하되 맥락과 함께 (예: "JPMorgan, 테슬라 매수 의견으로 상향")
- ~했다 / ~됐다 / ~한다 / ~임 으로 마무리
- 투자에 중요한 수치·사실 우선, 독자가 이해할 수 있는 완전한 문장

{sentences}"""


def translate_bullets(en_sentences, max_bullets=3):
    """영어 문장 리스트 → 한국어 불릿 리스트"""
    if not en_sentences or not _API_KEY:
        return []
    try:
        text = '\n'.join(f"- {s}" for s in en_sentences[:8])
        raw = _call(_US_BULLETS_PROMPT.format(max=max_bullets, sentences=text))
        parsed = _parse_numbered(raw)
        return (parsed or [])[:max_bullets]
    except Exception as e:
        print(f'  [Groq 불릿 오류] {e}')
        return []


# ── 한국어 문장들 → 불릿 정제 ────────────────────────────────────
_KO_BULLETS_PROMPT = """\
아래 한국어 뉴스 문장들에서 핵심 팩트를 추려 자연스러운 불릿으로 다듬어줘.
최대 {max}개, 번호 형식으로 출력. 설명 없이 결과만.

규칙:
- 45자 이내, "주어+내용" 구조로 — 너무 자르지 말고 의미가 전달되게
- 숫자·% 그대로 유지
- 자연스러운 한국어 문장으로 마무리 (~했다 / ~됐다 / ~한다 / ~될 전망)
- 광고·인용·평가 제외, 사실 정보만
- 부드럽고 읽기 편하게 — 전보 문체 금지

{sentences}"""


def refine_ko_bullets(ko_sentences, max_bullets=3):
    """한국어 문장 리스트 → 정제된 불릿 리스트"""
    if not ko_sentences or not _API_KEY:
        return []
    try:
        text = '\n'.join(f"- {s}" for s in ko_sentences[:8])
        raw = _call(_KO_BULLETS_PROMPT.format(max=max_bullets, sentences=text))
        parsed = _parse_numbered(raw)
        return (parsed or [])[:max_bullets]
    except Exception as e:
        print(f'  [Groq 한글 불릿 오류] {e}')
        return []


# ── Fallback: Google Translate ────────────────────────────────────
def _fallback_translate(titles):
    try:
        from deep_translator import GoogleTranslator
        out = []
        for t in titles:
            try:
                out.append(GoogleTranslator(source='en', target='ko').translate(t[:500]) or t)
            except Exception:
                out.append(t)
        return out
    except Exception:
        return titles
