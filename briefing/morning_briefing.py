import feedparser
import requests
import json
import os
import re
import sys
from datetime import datetime, timedelta
from llm_translate import (translate_titles, translate_bullets,
                           refine_ko_titles, refine_ko_bullets)

sys.stdout.reconfigure(encoding='utf-8')

import time as _time
import html as _html
from bs4 import BeautifulSoup

def get_us_bullets(url, title, summary, max_bullets=3):
    """미국 기사 URL → 한국어 불릿 리스트 (Groq LLM)"""
    sents = scrape_sentences(url)
    if sents and len(sents) >= 3:
        key = extract_key_facts(sents, max_sentences=6, title_hint=title)
    else:
        key = [f"{title}. {summary}"]
    return translate_bullets(key, max_bullets=max_bullets)


def get_ko_bullets(url, summary, title, max_bullets=3):
    """한국 기사 URL → 한국어 불릿 리스트 (Groq LLM)"""
    sents = scrape_sentences(url)
    ko_sents = [s for s in sents if len(s) >= 20] if sents else []
    if ko_sents:
        key = extract_key_facts_ko(ko_sents, max_sentences=5, title_hint=title)
    else:
        key = [summary] if summary else []
    return refine_ko_bullets(key, max_bullets=max_bullets)

def dedup_keywords(kws):
    """S&P / S&P 500 같은 중복 키워드 정리"""
    result, seen = [], set()
    for kw in kws:
        kl = kw.lower()
        if not any(kl in s or s in kl for s in seen):
            result.append(kw)
            seen.add(kl)
    return result

# ── 광고/구독 유도 문장 패턴 ──────────────────────────────────
AD_PATTERNS = re.compile(
    r'subscribe|newsletter|sign[\s-]?up|click here|sign in|log in|'
    r'every (monday|tuesday|wednesday|thursday|friday|saturday|sunday)|'
    r'inbox|delivered to|get this|join us|follow us|'
    r'advertisement|sponsored|affiliate|disclosure|'
    r'terms of (use|service)|privacy policy|cookie|'
    r'all rights reserved|copyright \d|'
    r'read (more|also|next)|related:|see also|more from|'
    r'twitter|facebook|instagram|linkedin|youtube|tiktok|'
    r'premium|exclusive|members only|free trial|paywall|'
    r'already a subscriber|become a member|'
    r"Lloyd.s List|로이드 리스트|세계에서 가장 오래된|무역 저널|"
    r'correction:|editor.s note:|this story has been',
    re.IGNORECASE
)

INVEST_KEYWORDS_EN = [
    'percent', '%', '$', 'billion', 'million', 'trillion',
    'stock', 'share', 'market', 'index', 'fund', 'ETF',
    'S&P', 'Nasdaq', 'Fed', 'rate', 'inflation', 'GDP',
    'TSMC', 'Micron', 'semiconductor', 'chip', 'AI',
    'earnings', 'revenue', 'profit', 'loss', 'quarter',
    'analyst', 'upgrade', 'downgrade', 'target', 'forecast',
    'rally', 'fell', 'rose', 'dropped', 'gained', 'decline',
    'investor', 'trade', 'tariff', 'economy', 'growth',
]

def scrape_sentences(url):
    """기사 URL → 문장 리스트 반환 (광고 제거 후)"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, 'lxml')

        for tag in soup(['script', 'style', 'nav', 'header', 'footer',
                         'aside', 'figure', 'figcaption', 'iframe', 'form',
                         'button', 'label', 'input']):
            tag.decompose()

        # 본문 영역 찾기
        paragraphs = []
        for selector in ['article', 'main', '[class*="article"]',
                         '[class*="body"]', '[class*="content"]', '[class*="story"]']:
            elem = soup.select_one(selector)
            if elem:
                paragraphs = elem.find_all('p')
                if len(paragraphs) >= 4:
                    break
        if not paragraphs:
            paragraphs = soup.find_all('p')

        sentences = []
        for p in paragraphs:
            text = re.sub(r'\s+', ' ', p.get_text(strip=True))
            # 광고·구독 유도 문장 제거
            if AD_PATTERNS.search(text):
                continue
            # 너무 짧은 조각 제거 (버튼 텍스트, 날짜 등)
            if len(text) < 50:
                continue
            # URL 포함된 문장 제거
            if re.search(r'https?://', text):
                continue
            sentences.append(text)
        return sentences
    except Exception as e:
        print(f"    [스크래핑 오류] {e}")
        return []

def extract_key_facts(sentences, max_sentences=6, title_hint=''):
    """투자 관련 핵심 문장 상위 N개 추출"""
    if not sentences:
        return []

    title_words = set(re.findall(r'\b[A-Za-z]{3,}\b', title_hint.lower())) if title_hint else set()

    def sentence_score(i, sent):
        s = sent.lower()
        score = 0
        if re.search(r'\d+\.?\d*\s*(%|billion|million|trillion|\$)', s):
            score += 3
        score += sum(1 for kw in INVEST_KEYWORDS_EN if kw.lower() in s)
        if title_words:
            score += sum(2 for w in title_words if w in s)
        if i < len(sentences) // 2:
            score += 1
        if 60 < len(sent) < 300:
            score += 1
        return score

    scored = sorted(
        enumerate(sentences),
        key=lambda x: sentence_score(x[0], x[1]),
        reverse=True
    )
    # 상위 N개 뽑되 원래 순서로 재정렬
    top_indices = sorted([i for i, _ in scored[:max_sentences]])
    return [sentences[i] for i in top_indices]

def build_article_summary(url, fallback_summary='', title_hint=''):
    """URL → 핵심 팩트 문장 영어 리스트 반환"""
    sentences = scrape_sentences(url)
    if sentences:
        return extract_key_facts(sentences, max_sentences=6, title_hint=title_hint)
    if fallback_summary:
        return [fallback_summary]
    return []

# ── 설정 로드 ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), 'r', encoding='utf-8') as f:
    config = json.load(f)

TELEGRAM_TOKEN   = config['telegram_token']
TELEGRAM_CHAT_ID = config['telegram_chat_id']
KR_COUNT         = config.get('korean_articles', 3)
US_COUNT         = config.get('us_articles', 3)

# ── 키워드 1순위: 포트폴리오 직결 ────────────────────────────
KR_PRIMARY = [
    '금리', '기준금리', '한국은행', '한은', 'KOSPI', '코스피',
    'KOSDAQ', '코스닥', '수출', '환율', '원달러', '무역수지',
    '물가', '인플레이션', 'GDP', '경제성장률'
]

US_PRIMARY = [
    "S&P", "S&P 500", "S&P500", "Nasdaq", "TSMC", "Micron", "MU",
    "GGLL", "IEMG", "Federal Reserve", "Fed", "interest rate",
    "semiconductor", "artificial intelligence", "AI", "emerging market",
    "inflation", "CPI", "tariff", "earnings"
]

# ── 키워드 2순위: 투자 관련 확장 스펙트럼 ──────────────────
KR_EXTENDED = KR_PRIMARY + [
    '실적', '영업이익', '매출', '주가', '증시', '시가총액',
    '외국인', '기관', '순매수', '순매도', '공시',
    '삼성전자', 'SK하이닉스', '반도체', '배당', '유상증자',
    '재정', '국채', '기업', '투자', '경기', '소비자물가',
    'PPI', '생산자물가', '고용', '실업률', '부동산'
]

US_EXTENDED = US_PRIMARY + [
    "earnings", "revenue", "guidance", "outlook", "beat", "miss",
    "analyst", "upgrade", "downgrade", "price target", "rating",
    "merger", "acquisition", "buyback", "dividend", "IPO",
    "rally", "selloff", "correction", "bull", "bear",
    "jobs", "unemployment", "payroll", "consumer", "retail",
    "chip", "data center", "cloud", "tech", "growth",
    "oil", "energy", "dollar", "DXY", "yield", "treasury",
    "recession", "GDP", "PMI", "housing", "manufacturing",
    "Apple", "Microsoft", "Google", "Alphabet", "Amazon", "Meta",
    "Nvidia", "NVDA", "AMD", "Intel", "Qualcomm", "Samsung"
]

# ── 한국 기사 비투자 뉴스 필터 (AP 검수) ──────────────────────
KR_EXCLUDE = re.compile(
    # 부고·인물
    r'부친상|모친상|부고|별세|타계|조문|장례|추모|영결식|빈소|'
    # 지역행정
    r'농수산식품|관광\s*협약|교육청|복지관|의료원|'
    r'(?:전남|전북|경남|경북|충남|충북|강원도|제주도)\s*(?:지사|도청|협약|협력)|'
    r'(?:도지사|구청장|군수)\s|'
    # 스포츠
    r'야구|축구|농구|골프|올림픽|월드컵|스포츠|선수|감독|'
    # 연예·문화
    r'연예인|드라마|영화|K팝|콘서트|공연|뮤지컬|배우|가수|'
    # 기타 비투자
    r'맛집|요리|레시피|패션|뷰티|여행|날씨|미세먼지',
    re.IGNORECASE
)

# 한국 기사 투자 관련 스코어링 키워드
INVEST_KEYWORDS_KO = [
    '%', '억원', '조원', '달러', '금리', '기준금리', '환율', '원달러',
    'KOSPI', '코스피', 'KOSDAQ', '코스닥', '주가', '증시', '지수',
    '상승', '하락', '급등', '급락', '강세', '약세',
    '수출', '무역수지', '물가', 'CPI', 'GDP', '경상수지',
    '반도체', '삼성전자', 'SK하이닉스', 'AI', '인공지능',
    '한국은행', '연준', '금리인상', '금리인하', '긴축', '완화',
    '실적', '영업이익', '매출', '순이익', '외국인', '기관',
]

# ── RSS 피드 ───────────────────────────────────────────────
KOREAN_FEEDS = [
    ('연합뉴스', 'https://www.yna.co.kr/rss/economy.xml'),
    ('한국경제', 'https://www.hankyung.com/rss/feed_economy.xml'),
    ('이데일리', 'https://rss2.edaily.co.kr/economy.xml'),
    ('머니투데이', 'https://rss.mt.co.kr/mt_economy.xml'),
]

US_FEEDS = [
    ('Reuters',       'https://feeds.reuters.com/reuters/businessNews'),
    ('AP',            'https://feeds.apnews.com/apf-business'),
    ('WSJ',           'https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml'),
    ('CNBC',          'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114'),
    ('MarketWatch',   'https://feeds.content.dowjones.io/public/rss/mw_topstories'),
    ('Yahoo Finance', 'https://finance.yahoo.com/rss/topfinstories'),
    ('Investing.com', 'https://www.investing.com/rss/news.rss'),
]

# 화이트리스트: 허용 소스만 통과 — 출처 불명·블로그성 자동 차단
KR_WHITELIST = frozenset(['연합뉴스', '한국경제', '이데일리', '머니투데이'])
US_WHITELIST = frozenset(['Reuters', 'AP', 'WSJ', 'CNBC', 'MarketWatch', 'Yahoo Finance', 'Investing.com'])

# ── 유틸 ────────────────────────────────────────────────────
def clean_html(text):
    return re.sub(r'<[^>]+>', '', text or '').strip()

def score(title, summary, keywords):
    text = (title + ' ' + summary).lower()
    matched = [kw for kw in keywords if kw.lower() in text]
    return len(matched), matched

def is_duplicate(new_title, selected):
    new_words = set(new_title.lower().split())
    for a in selected:
        overlap = len(new_words & set(a['title'].lower().split()))
        if overlap >= 4:
            return True
    return False

def get_pub_dt(entry):
    """기사 발행 시각 반환. 없으면 None."""
    import time as _time
    for field in ('published_parsed', 'updated_parsed'):
        parsed = entry.get(field)
        if parsed:
            try:
                return datetime.fromtimestamp(_time.mktime(parsed))
            except Exception:
                pass
    return None

def collect_raw(feeds, hours=24, whitelist=None):
    """피드에서 최근 hours시간 이내 기사 전량 수집 (화이트리스트 소스만)"""
    raw = []
    cutoff = datetime.now() - timedelta(hours=hours)
    for source, url in feeds:
        if whitelist and source not in whitelist:
            continue  # 화이트리스트 외 소스 자동 차단
        try:
            feed = feedparser.parse(url, request_headers={'User-Agent': 'Mozilla/5.0'})
            for entry in feed.entries[:40]:
                pub_dt = get_pub_dt(entry)
                if pub_dt and pub_dt >= cutoff:
                    raw.append({
                        'source': source,
                        'title':  entry.get('title', ''),
                        'summary': clean_html(entry.get('summary', entry.get('description', '')))[:200],
                        'link':   entry.get('link', ''),
                        'published': pub_dt,
                    })
        except Exception as e:
            print(f"  [경고] {source}: {e}")
    return raw

# ── 기사 수집 (24시간 고정 + 키워드 스펙트럼 확장) ──────────
def fetch_articles(feeds, primary_kw, extended_kw, count, exclude=None, whitelist=None):
    raw = collect_raw(feeds, hours=24, whitelist=whitelist)
    print(f"    24시간 이내 원본 기사 수: {len(raw)}건")

    def rank(articles, keywords):
        scored = []
        for a in articles:
            if exclude and exclude.search(a['title'] + ' ' + a['summary']):
                continue
            s, matched = score(a['title'], a['summary'], keywords)
            if s > 0:
                scored.append({**a, 'score': s, 'keywords': matched[:4]})
        scored.sort(key=lambda x: (x['score'], x['published']), reverse=True)
        selected = []
        for a in scored:
            if len(selected) >= count:
                break
            if not is_duplicate(a['title'], selected):
                selected.append(a)
        return selected

    # 1차: 포트폴리오 직결 키워드
    selected = rank(raw, primary_kw)

    # 부족하면 2차: 확장 키워드로 나머지 채우기 (시간은 그대로 24시간)
    if len(selected) < count:
        existing_links = {a['link'] for a in selected}
        remaining = [a for a in raw if a['link'] not in existing_links]
        extra = rank(remaining, extended_kw)
        for a in extra:
            if len(selected) >= count:
                break
            if not is_duplicate(a['title'], selected):
                selected.append(a)
        if len(selected) > 0:
            print(f"    확장 키워드로 {len(selected)}건 확보")

    if not selected:
        print(f"  [주의] 오늘 관련 기사 없음 — 피드 상태 점검 필요")

    return selected

def h(text):
    """HTML 특수문자 이스케이프"""
    return str(text).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

# ── 원화 강세/약세 명시 패턴 ──────────────────────────────────
# 실제 기사에 등장하는 표현만 포함 (대화 표현 X)
_WON_STRONG_RE = re.compile(
    r'원화\s*강세|원화\s*절상|달러\s*약세|원달러.*하락|달러인덱스.*하락|'
    r'won.*strong|dollar.*weak|dollar.*fall|dollar.*drop|DXY.*fall',
    re.IGNORECASE
)
_WON_WEAK_RE = re.compile(
    r'원화\s*약세|원화\s*절하|달러\s*강세|원달러.*상승|달러인덱스.*상승|'
    r'won.*weak|dollar.*strong|dollar.*rise|dollar.*surge|DXY.*rise',
    re.IGNORECASE
)

# ── 투자 시사점 감성 감지 ─────────────────────────────────────
_BULL_RE = re.compile(
    r'상승|급등|강세|호조|반등|돌파|최고|급증|회복|확대|개선|수혜|인하|완화|dovish|'
    r'rally|surge|rose|gain|record|beat|strong|bullish|growth|boost|rise|cut|easing',
    re.IGNORECASE
)
_BEAR_RE = re.compile(
    r'하락|급락|약세|침체|우려|부진|손실|악화|최저|둔화|긴축|위기|인상|hawkish|'
    r'fell|drop|decline|crash|miss|weak|bearish|risk|concern|recession|selloff|hike|tighten',
    re.IGNORECASE
)

def _sentiment(combined):
    up = len(_BULL_RE.findall(combined))
    dn = len(_BEAR_RE.findall(combined))
    if up > dn:   return '📈'
    elif dn > up: return '📉'
    else:         return '➡'

def _impl_fed(txt):
    t = txt.lower()
    if any(k in t for k in ['인하', 'cut', 'lower', 'dove', 'pause', 'hold', 'easing', '완화']):
        return ("금리 인하 기대 → DCF 할인율↓ → TSMC·MU·GGLL 성장주 밸류에이션 상승 압력",
                "TSM·MU·GGLL 비중 유지, 조정 시 추가 매수 기회 탐색")
    if any(k in t for k in ['인상', 'hike', 'raise', 'hawk', '긴축', 'tighten']):
        return ("금리 인상 신호 → DCF 할인율↑ → 성장주 멀티플 압박",
                "GGLL 레버리지 비중 선제 축소, TSM·MU 손절 기준 재확인")
    return ("연준 스탠스 불투명, 발표 당일 성장주 변동성 경계",
            "포지션 변경 없이 연준 발언 확인 후 대응")

def _impl_inflation(txt):
    t = txt.lower()
    if any(k in t for k in ['상승', 'rise', 'hot', 'above', '높', 'surge', 'accelerat']):
        return ("물가 상승 → 연준 긴축 지속 → 성장주 하락 압력",
                "GGLL 비중 축소 검토, TSM·MU 손절 기준 타이트하게 조정")
    if any(k in t for k in ['하락', 'cool', 'below', '둔화', 'easing', 'slow', 'declin']):
        return ("물가 둔화 → 연준 피벗 기대 → 할인율↓, TSMC·MU·GGLL 수혜",
                "GGLL 비중 확대 검토, TSM·MU 비중 유지")
    return ("물가 방향 혼재, 발표 당일 변동성 주의",
            "포지션 유지, 연준 반응 확인 후 조정")

def _impl_semi(txt):
    t = txt.lower()
    if any(k in t for k in ['ai', 'hbm', 'data center', '수요', 'demand', 'cowos']):
        return ("AI·HBM 수요 급증 → 파운드리 풀가동·HBM 출하 확대 → 실적 상향 가능",
                "TSM·MU 비중 유지, 실적 발표 전 추가 매수 기회 탐색")
    if any(k in t for k in ['oversupply', 'inventory', '재고', '공급 과잉', 'glut']):
        return ("반도체 재고 과잉 → ASP·마진 압박 → 단기 실적 하향 위험",
                "MU 추가 매수 신중, 재고 정상화 확인 전 비중 축소 검토")
    return ("반도체 흐름 중립, 세부 수요·공급 동향 확인 필요",
            "TSM·MU 현 포지션 유지, 원문에서 세부 내용 확인 권장")

def _impl_index(txt):
    t = txt.lower()
    if any(k in t for k in ['record', 'high', '최고', '사상', '신고', 'rally', 'surge', 'rose']):
        return ("지수 신고가 → SPY 직접 수익, 보유 성장주 동반 상승 기대",
                "SPY 비중 유지, 성장주(TSM·MU·GGLL) 강세 모멘텀 추종")
    if any(k in t for k in ['fell', 'drop', 'decline', '하락', '급락', 'selloff', 'correction']):
        return ("지수 하락 → 보유 종목 전반 조정 압력",
                "손절 기준 재확인, GGLL 레버리지 낙폭 주시 — 필요 시 선제 비중 축소")
    return ("S&P500 방향 중립, 섹터 로테이션·거래량 동향 주시",
            "SPY 현 포지션 유지")

def _impl_trade(txt):
    t = txt.lower()
    if any(k in t for k in ['협상', 'deal', 'agreement', '타결', '완화', 'exemption', 'truce']):
        return ("무역 협상 진전 → 신흥국 리스크↓, 반도체 공급망 불확실성 완화",
                "IEMG 비중 유지, TSM 공급망 우려 해소 시 추가 매수 검토")
    return ("무역 갈등 격화 → 반도체 공급망 타격, 신흥국 자금 이탈 주의",
            "IEMG 손절 기준 재확인, TSM 수출 규제 동향 모니터링")

# (keyword_list, impl_function_or_string)
_IMPL_RULES = [
    (['TSMC', 'TSM', '파운드리', 'foundry'],
     lambda t: (
         ("기관 매도 신호 → 단기 하방 압력",
          "TSM 비중 축소 또는 손절 기준 재설정") if any(k in t.lower() for k in ['매도', 'sell', 'sold', 'exit', 'reduce'])
         else ("기관 매수 신호 → 파운드리 수요 확인, 모멘텀 강화",
               "TSM 비중 유지") if any(k in t.lower() for k in ['매수', 'buy', 'bought', 'add', 'increase'])
         else ("AI·CoWoS 수요 급증 → 가동률·마진 개선 → 주가 상승 압력",
               "TSM 비중 유지, 추가 매수 검토") if _sentiment(t) == '📈'
         else ("수주 둔화·가동률 하락 → 단기 조정 가능",
               "TSM 신규 매수 보류, 손절 기준 확인")
     )),
    (['Micron', 'MU', 'DRAM', 'HBM', 'NAND', 'memory'],
     lambda t: (
         ("기관 매도 신호 → 단기 하방 압력",
          "MU 비중 축소, 실적 발표 전 손절 기준 재점검") if any(k in t.lower() for k in ['매도', 'sell', 'sold', 'exit', 'reduce', 'cut'])
         else ("기관 매수 신호 → HBM·AI 수요 확인, 모멘텀 강화",
               "MU 비중 유지") if any(k in t.lower() for k in ['매수', 'buy', 'bought', 'add', 'increase', 'purchase'])
         else ("HBM 출하 확대·ASP 상승 → 실적 상향 기대",
               "MU 보유 유지, 실적 발표 전 비중 점검") if _sentiment(t) == '📈'
         else ("재고 사이클 부담 → ASP·마진 압박",
               "MU 추가 매수 신중, 실적 확인 후 판단")
     )),
    (['Alphabet', 'Google', 'GOOGL', 'GGLL'],
     lambda t: (
         ("광고·AI 매출 호조 → Alphabet 주가↑ → 2배 레버리지 수익 배가",
          "GGLL 비중 유지, 레버리지 수익 극대화") if _sentiment(t) == '📈'
         else ("실적 실망 또는 악재 → Alphabet 주가↓ → 레버리지 낙폭 2배",
               "GGLL 즉시 비중 축소 검토, 손절 기준 재확인")
     )),
    (['신흥국', 'IEMG', 'emerging market', 'EM'],
     lambda t: (
         ("달러 약세·위험선호 → 신흥국 자금 유입 → IEMG 상승 기대",
          "IEMG 비중 유지, 추가 매수 기회 탐색") if _sentiment(t) == '📈'
         else ("달러 강세·위험회피 → 신흥국 자금 이탈 → IEMG 하락 압력",
               "IEMG 손절 기준 재확인, 비중 축소 검토")
     )),
    (['Fed', '연준', 'Federal Reserve', 'interest rate', '금리', '기준금리'], _impl_fed),
    (['CPI', '인플레이션', 'inflation', '물가', 'PPI', 'PCE'], _impl_inflation),
    (['반도체', 'semiconductor', 'chip', 'AI chip', 'wafer'], _impl_semi),
    (['AI', '인공지능', 'artificial intelligence', 'data center', 'GPU', 'Nvidia'],
     lambda t: ("AI 인프라 투자 확대 → 파운드리·HBM 수요 증가 → 실적 개선 기대",
                "TSM·MU 비중 유지, AI 수요 모멘텀 지속 시 추가 매수 검토")),
    (['관세', 'tariff', '무역전쟁', 'trade war', 'trade deal'], _impl_trade),
    (['S&P', 'S&P500', '나스닥', 'Nasdaq', '증시', 'stock market'], _impl_index),
    (['달러', 'dollar', 'DXY', '환율', 'currency', '원달러', '원화'],
     lambda t: (
         ("달러 약세(원화 강세) → 달러 자산 원화 환산 수익률↓",
          "환차손 점검 — 달러 자산 비중 과다 시 일부 원화 전환 고려") if _WON_STRONG_RE.search(t)
         else ("달러 강세(원화 약세) → 달러 자산 원화 환산 수익률↑",
               "환차익 유지 — 달러 자산 비중 현행 유지 또는 확대 검토") if _WON_WEAK_RE.search(t)
         else ("환율 방향 불확실, 달러 강세 시 수익↑·약세 시 수익↓",
               "환율 직접 확인 후 방향성 판단")
     )),
    (['GDP', '경기침체', 'recession', '경제성장', 'growth'],
     lambda t: (
         ("경기 확장 지속 → 기업 실적 개선 기대, 성장주 유리",
          "TSM·MU·GGLL 성장주 비중 유지, 강세 모멘텀 활용") if _sentiment(t) == '📈'
         else ("경기 침체 우려 → 성장주 리스크↑",
               "방어적 포지션 전환 검토 — GGLL 비중 축소, 현금 비중 확대")
     )),
    (['KOSPI', '코스피', '코스닥', 'KOSDAQ', '이재명'],
     lambda t: (
         ("코스피 강세 → 통상 원화 강세 동반 → 달러 자산 원화 환산 수익률↓",
          "환차손 점검 — 원화 강세 정도 확인 후 환율 헷지 여부 판단") if _sentiment(t) == '📈'
         else ("국내 증시 약세 → 글로벌 위험회피 심리 확산, 미국 성장주 동반 조정 가능",
               "미국 성장주 변동성 대비 — GGLL 비중 점검")
     )),
    (['한국은행', '한은'],
     lambda t: (
         ("한은 금리 인상 → 원화 강세 압력 → 달러 자산 원화 환산 수익률↓",
          "환차손 점검 — 원화 강세 장기화 시 일부 원화 전환 고려") if any(k in t.lower() for k in ['인상', 'hike', 'raise', '긴축'])
         else ("한은 금리 인하 → 원화 약세 압력 → 달러 자산 원화 환산 수익률↑",
               "환차익 유지 — 달러 자산 비중 현행 유지") if any(k in t.lower() for k in ['인하', 'cut', 'lower', '완화'])
         else ("한은 스탠스 확인 필요, 금리 방향에 따라 환율 변동 주시",
               "금리 결정 확인 후 환율 방향 판단")
     )),
]

# ── 기사 신뢰도 스코어링 ─────────────────────────────────────────────
_SOURCE_TIER = {
    'Reuters': 3, 'AP': 3, 'WSJ': 3, 'Financial Times': 3,
    'CNBC': 2, 'MarketWatch': 2, 'Yahoo Finance': 2, 'Investing.com': 2,
    '연합뉴스': 2, '한국경제': 2, '이데일리': 2, '머니투데이': 2,
}

def credibility_score(article):
    """기사 신뢰도 1~5점 반환 (소스등급 + 최신성 + 팩트밀도)"""
    pts = 0
    # ① 소스 신뢰도 (1~3점)
    pts += _SOURCE_TIER.get(article['source'], 1)
    # ② 최신성 — 발행 시각 기준 (1~3점)
    age_h = (datetime.now() - article['published']).total_seconds() / 3600
    pts += 3 if age_h < 3 else (2 if age_h < 8 else 1)
    # ③ 팩트 밀도 — 수치·핵심 고유명사 (0~3점)
    text = (article['title'] + ' ' + article['summary']).lower()
    nums  = len(re.findall(r'\d+\.?\d*\s*[%$]|\$\d|\d+\s*(billion|million|억|조)', text))
    names = len(re.findall(r'\b(tsmc|micron|fed|s&p|nasdaq|fomc|cpi|gdp|nfp|hbm)\b', text))
    pts += min(3, nums + (1 if names > 0 else 0))
    # ④ 제목 품질 패널티
    t = article['title']
    if t.isupper() or t.count('!') > 1 or len(t) > 130:
        pts -= 1
    return max(1, round(max(1, min(9, pts)) / 9 * 5))

def cred_bar(score):
    return '●' * score + '○' * (5 - score)


# ── Groq 기반 기사 분석 ([요약]/[선행신호]/[판단]) ───────────────────────
_PORTFOLIO_DESC = (
    'TSM(TSMC 파운드리), MU(Micron HBM), GGLL(Alphabet 2x레버리지), '
    'IEMG(신흥국ETF), SPYM(S&P500 ETF), QLD(나스닥100 2x레버리지)'
)

_GROQ_ANALYSIS_PROMPT = """\
기사 제목: {title}
기사 요약: {summary}
GM Capital 보유종목: {portfolio}

아래 형식으로만 응답. 다른 텍스트 없이:

[요약] 핵심 2~3줄
[선행신호] 있음 — 구체 내용 / 없음
[판단] 매수관심 / 관망 / 모니터링 / 판단불가

원칙:
- 선행신호: 주가에 아직 미반영된 정보일 때만 있음
- 보유종목과 관련 없으면 판단불가
- 애매하면 판단불가 (억지 결론 금지)"""

def get_groq_analysis(title, summary=''):
    try:
        raw = groq_client.call(
            _GROQ_ANALYSIS_PROMPT.format(
                title=title,
                summary=(summary[:400] if summary else '없음'),
                portfolio=_PORTFOLIO_DESC,
            ),
            max_tokens=280,
            temperature=0.2,
        )
        return raw.strip()
    except Exception as e:
        print(f'    [Groq 분석 오류] {e}')
        return ''


def get_implication(title, summary=''):
    combined = title + ' ' + summary
    title_lower = title.lower()
    combined_lower = combined.lower()

    def _fmt(arrow, result):
        if isinstance(result, tuple):
            market, action = result
            return f"{arrow} {market}\n📌 {action}"
        return f"{arrow} {result}"

    # Tier 1 — 직접 보유 종목: 제목에 키워드 있을 때만 매칭
    for keywords, impl_rule in _IMPL_RULES[:4]:
        if any(kw.lower() in title_lower for kw in keywords):
            return _fmt(_sentiment(combined),
                        impl_rule(combined) if callable(impl_rule) else impl_rule)

    # Tier 2/3 — 섹터·매크로: 제목 또는 요약 전체에서 매칭
    for keywords, impl_rule in _IMPL_RULES[4:]:
        if any(kw.lower() in combined_lower for kw in keywords):
            return _fmt(_sentiment(combined),
                        impl_rule(combined) if callable(impl_rule) else impl_rule)

    return ''

# ── S&P500 섹터 등락 ──────────────────────────────────────────
# 섹터별 후보 종목 — 당일 ETF 방향과 동일하게 움직인 최대 변동 종목을 대표로 선택
_SECTORS = [
    ('XLK',  '기술',        ['NVDA', 'AAPL', 'MSFT']),
    ('XLC',  '커뮤니케이션', ['META', 'GOOGL', 'NFLX']),
    ('XLY',  '임의소비재',  ['AMZN', 'TSLA', 'HD']),
    ('XLF',  '금융',        ['JPM', 'GS', 'BAC']),
    ('XLV',  '헬스케어',    ['LLY', 'UNH', 'JNJ']),
    ('XLI',  '산업',        ['GE', 'CAT', 'HON']),
    ('XLE',  '에너지',      ['XOM', 'CVX', 'COP']),
    ('XLB',  '소재',        ['LIN', 'APD', 'NEM']),
    ('XLP',  '필수소비재',  ['WMT', 'COST', 'PG']),
    ('XLRE', '부동산',      ['PLD', 'AMT', 'CCI']),
    ('XLU',  '유틸리티',   ['NEE', 'SO', 'DUK']),
]

def get_sector_performance():
    try:
        import yfinance as yf
        all_etfs    = [s for s, _, _ in _SECTORS]
        all_stocks  = list({stk for _, _, cands in _SECTORS for stk in cands})
        syms        = all_etfs + all_stocks
        data        = yf.download(syms, period='2d', progress=False, auto_adjust=True)['Close']
        results     = []
        for sym, name, candidates in _SECTORS:
            try:
                etf_chg = float((data[sym].iloc[-1] - data[sym].iloc[-2]) / data[sym].iloc[-2] * 100)
                # ETF 방향과 일치하는 종목 중 변동폭 최대인 종목을 대표로 선택
                best_rep, best_chg = candidates[0], 0.0
                for cand in candidates:
                    try:
                        c_chg = float((data[cand].iloc[-1] - data[cand].iloc[-2]) / data[cand].iloc[-2] * 100)
                        same_dir = (etf_chg >= 0) == (c_chg >= 0)
                        cur_same = (etf_chg >= 0) == (best_chg >= 0)
                        if same_dir and (not cur_same or abs(c_chg) > abs(best_chg)):
                            best_rep, best_chg = cand, c_chg
                    except:
                        continue
                if best_chg == 0.0:
                    best_chg = float((data[candidates[0]].iloc[-1] - data[candidates[0]].iloc[-2])
                                     / data[candidates[0]].iloc[-2] * 100)
                    best_rep = candidates[0]
                results.append((name, best_rep, etf_chg, best_chg))
            except:
                pass
        results.sort(key=lambda x: x[2], reverse=True)
        return results
    except Exception as e:
        print(f"  [섹터 오류] {e}")
        return []

# ── 한국 기사 스크래핑 + 팩트 추출 ──────────────────────────
def extract_key_facts_ko(sentences, max_sentences=5, title_hint=''):
    """한국어 문장에서 투자 핵심 팩트 추출"""
    if not sentences:
        return []

    title_words = set(re.findall(r'[가-힣]{2,}', title_hint)) if title_hint else set()

    def score_ko(i, sent):
        s = 0
        if re.search(r'\d+\.?\d*\s*(?:%|억|조|원|달러|bp)', sent):
            s += 3
        s += sum(1 for kw in INVEST_KEYWORDS_KO if kw in sent)
        if title_words:
            s += sum(2 for w in title_words if w in sent)
        if i < len(sentences) // 2:
            s += 1
        if 20 < len(sent) < 200:
            s += 1
        return s

    scored = sorted(enumerate(sentences), key=lambda x: score_ko(x[0], x[1]), reverse=True)
    top = sorted([i for i, _ in scored[:max_sentences]])
    return [sentences[i] for i in top]

def build_article_summary_ko(url, fallback='', title_hint=''):
    """한국어 기사 URL → 핵심 팩트 문장 리스트"""
    sentences = scrape_sentences(url)
    ko_sents = [s for s in sentences if len(s) >= 20] if sentences else []
    if ko_sents:
        return extract_key_facts_ko(ko_sents, max_sentences=5, title_hint=title_hint)
    if fallback:
        return [fallback]
    return []

# ── 텔레그램 메시지 포맷 (HTML 모드) ──────────────────────────
def format_message(kr, us):
    now    = datetime.now()
    day_kr = ['월','화','수','목','금','토','일'][now.weekday()]

    lines = []
    lines.append(f"📊 <b>GM Capital 모닝 브리핑</b>")
    lines.append(f"{now.strftime('%Y년 %m월 %d일')} ({day_kr}요일)\n")

    # ── S&P500 섹터 등락 ──
    sectors = get_sector_performance()
    if sectors:
        top3 = sectors[:3]
        bot3 = sectors[-3:]
        lines.append("🗂 <b>S&amp;P500 섹터 등락</b>")
        lines.append("─" * 18)
        def fmt_s(n, r, ec, rc):
            es = f"{'+' if ec>=0 else ''}{ec:.1f}%"
            rs = f"{'+' if rc>=0 else ''}{rc:.1f}%"
            return f"{n} {es} ({r} {rs})"
        lines.append("▲ " + "  ".join(fmt_s(*s) for s in top3))
        lines.append("▼ " + "  ".join(fmt_s(*s) for s in bot3))
        lines.append("")

    # ── 한국 시장 ──
    lines.append("🇰🇷 <b>한국 시장</b>")
    lines.append("─" * 18)

    print("    [LLM] 한국 기사 제목 정제 중...")
    _kr_titles = refine_ko_titles([a['title'] for a in kr])

    for i, (a, title_kr) in enumerate(zip(kr, _kr_titles), 1):
        kws  = ' · '.join(a['keywords'][:3])
        cred = cred_bar(credibility_score(a))
        lines.append(f"\n<b>{i}. {h(title_kr)}</b>")
        lines.append(f"<i>{h(a['source'])}</i>  {h(kws)}  <b>{cred}</b>")

        bullets_ko = get_ko_bullets(a['link'], a['summary'], a['title'])
        for b in bullets_ko:
            lines.append(f"• {h(b)}")

        analysis = get_groq_analysis(a['title'], a['summary'])
        if analysis:
            lines.append(f"<i>{h(analysis)}</i>")
        lines.append(f'<a href="{a["link"]}">▶ 기사 보기</a>')
        _time.sleep(0.3)

    # ── 미국 시장 (한국어 번역) ──
    lines.append("\n🇺🇸 <b>미국 시장</b> (한국어 번역)")
    lines.append("─" * 18)

    print("    [LLM] 미국 기사 제목 번역 중...")
    _us_titles_ko = translate_titles([a['title'] for a in us])

    for i, (a, title_ko) in enumerate(zip(us, _us_titles_ko), 1):
        kws  = ' · '.join(dedup_keywords(a['keywords'])[:3])
        cred = cred_bar(credibility_score(a))
        print(f"    [처리중] 미국 기사 {i}: {a['title'][:45]}...")

        lines.append(f"\n<b>{i}. {h(title_ko or a['title'])}</b>")
        lines.append(f"<i>{h(a['source'])}</i>  {h(kws)}  <b>{cred}</b>")

        # 뱃치 번역으로 불릿 생성 (문맥 보존, 1회 API 호출)
        bullets = get_us_bullets(a['link'], a['title'], a['summary'])
        for b in bullets:
            lines.append(f"• {h(b)}")
        _time.sleep(0.5)

        analysis_us = get_groq_analysis(a['title'], a['summary'])
        if analysis_us:
            lines.append(f"<i>{h(analysis_us)}</i>")
        lines.append(f'<a href="{a["link"]}">▶ 원문 보기</a>')

    lines.append("\n" + "─" * 16)
    lines.append("🏢 <i>GM Capital · INF → DAT → AP → ROY</i>")
    return '\n'.join(lines)

# ── 텔레그램 전송 ────────────────────────────────────────────
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }, timeout=15)
    return resp.json()

# ── 메인 ────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] 브리핑 수집 시작...")

    kr = fetch_articles(KOREAN_FEEDS, KR_PRIMARY, KR_EXTENDED, KR_COUNT, exclude=KR_EXCLUDE, whitelist=KR_WHITELIST)
    us = fetch_articles(US_FEEDS,     US_PRIMARY, US_EXTENDED, US_COUNT, whitelist=US_WHITELIST)

    print(f"  한국 기사: {len(kr)}개 / 미국 기사: {len(us)}개")

    if not kr and not us:
        send_telegram("⚠️ *GM Capital*: 오늘 브리핑 수집 실패. 뉴스 소스를 확인해주세요.")
        return

    msg    = format_message(kr, us)
    result = send_telegram(msg)

    if result.get('ok'):
        print("✅ 브리핑 전송 완료!")
    else:
        print(f"❌ 전송 실패: {result}")

    # 발굴 엔진 — 신호 먼저 구조 (보유종목 발굴 제외)
    print("\n[발굴 스캔 시작]")
    try:
        from discovery import run_discovery
        run_discovery()
    except Exception as _e:
        print(f"  [발굴 오류] {_e}")

    # 보유종목 모니터링 (긴급도 높음만 알림)
    print("\n[보유종목 모니터링]")
    try:
        from holdings_monitor import run_holdings_monitor
        run_holdings_monitor()
    except Exception as _e:
        print(f"  [보유종목 모니터링 오류] {_e}")

if __name__ == '__main__':
    main()
