from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, quote_plus

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports"
STATE_DIR = ROOT / "state"
STATE_PATH = STATE_DIR / "valuation_methods.json"

KST = dt.timezone(dt.timedelta(hours=9), name="Asia/Seoul")
TODAY_KST = dt.datetime.now(KST).date()

USER_AGENT = (
    "Mozilla/5.0 (compatible; valuation-method-watch/1.0; "
    "+https://github.com; public report monitor)"
)

# ── 검색 쿼리 ──────────────────────────────────────────────────────────────

BROKER_QUERIES = [
    '"PBR 대신 PER" "목표주가"',
    '"PBR에서 PER" "목표주가"',
    '"목표주가 산정" "BPS" "PBR" "EPS" "PER"',
    '"목표가 산정" "BPS" "PBR" "EPS" "PER"',
    '"Target PBR" "Target PER" "목표주가"',
    '"BPS" "Target PBR" "목표주가" "EPS"',
    '"EPS" "Target PER" "목표주가" "PBR"',
    '"밸류에이션 변경" "목표주가" "PBR" "PER"',
    '"SOTP" "목표주가" "PBR" "PER" "증권"',
    '"EV/EBITDA" "목표주가" "PBR" "PER" "증권"',
]

# 커뮤니티·뉴스·인터넷 추가 쿼리
COMMUNITY_QUERIES = [
    "PBR 대신 PER 밸류에이션 전환 종목",
    "목표주가 산정 PER 변경 증권사",
    "밸류에이션 방법 PBR에서 PER 변경",
    "실적 개선 PBR PER 전환 추천",
    '"PBR" "PER" "밸류에이션" "전환" 종목',
    "증권사 리포트 밸류에이션 변경 추천",
    '"EPS" "PER" 목표주가 상향 증권',
    '"SOTP" 밸류에이션 목표주가 증권',
    "네이버 카페 PBR PER 전환 종목 토론",
    "주식 커뮤니티 밸류에이션 변경 추천 종목",
]

QUERY_TEMPLATES = BROKER_QUERIES + COMMUNITY_QUERIES

# 네이버 뉴스 직접 검색 쿼리
NAVER_NEWS_QUERIES = [
    "PBR 대신 PER 목표주가",
    "밸류에이션 전환 PBR PER 증권사",
    "목표주가 산정방식 변경",
    "EPS PER 목표주가 상향",
    "BPS PBR PER 전환 종목",
]

BROKERS = [
    "SK증권", "하나증권", "한국투자증권", "KB증권", "NH투자증권",
    "미래에셋증권", "대신증권", "키움증권", "유진투자증권", "한화투자증권",
    "메리츠증권", "삼성증권", "신한투자증권", "iM증권", "IBK투자증권",
    "DB금융투자", "교보증권", "LS증권", "다올투자증권", "BNK투자증권",
    "상상인증권", "유안타증권", "현대차증권",
]

KNOWN_STOCK_NAMES = [
    "삼성전자", "SK하이닉스", "삼성전기", "LG에너지솔루션", "POSCO홀딩스",
    "현대차", "기아", "NAVER", "카카오", "셀트리온", "삼성바이오로직스",
    "한화에어로스페이스", "HD현대중공업", "한국전력", "아모레퍼시픽",
    "LG생활건강", "한국콜마", "두산에너빌리티", "LG전자", "현대모비스",
    "삼성SDI", "LG화학", "SK이노베이션", "롯데케미칼", "포스코퓨처엠",
]

SWITCH_PATTERNS = [
    r"PBR.{0,30}대신.{0,30}PER",
    r"PBR.{0,30}에서.{0,30}PER",
    r"PBR.{0,40}PER.{0,20}전환",
    r"PBR.{0,40}아니라.{0,30}PER",
    r"주가순자산비율.{0,30}대신.{0,30}주가수익비율",
    r"밸류에이션\s*방법.{0,30}(변경|전환)",
    r"산정\s*방식.{0,30}(변경|전환)",
]

PBR_PATTERNS = [
    r"Target\s*PBR", r"목표\s*PBR", r"적정\s*PBR",
    r"BPS.{0,30}PBR", r"PBR.{0,30}BPS",
    r"예상\s*BPS", r"12M.{0,10}BPS",
]

PER_PATTERNS = [
    r"Target\s*PER", r"목표\s*PER", r"적정\s*PER",
    r"EPS.{0,30}PER", r"PER.{0,30}EPS",
    r"예상\s*EPS", r"12M.{0,10}EPS",
]

SOTP_PATTERNS = [
    r"SOTP", r"사업부별\s*가치", r"가치\s*합산", r"EV/EBITDA",
]

# 시클리컬(수주·경기민감) 산업 키워드 — 빅사이클 법칙이 가장 잘 적용되는 곳
CYCLICAL_KEYWORDS = [
    "조선", "변압기", "중전기", "전력망", "전력기기", "송배전",
    "방산", "방위", "무기", "함정", "건설기계", "굴착기",
    "정유", "석유화학", "화학", "철강", "비철", "해운", "벌크",
    "반도체", "메모리", "파운드리", "풍력", "원전", "원자력",
    "수주", "수주잔고", "수주잔량", "턴어라운드", "업황 회복", "슈퍼사이클",
]

# ── 도메인 필터 ──────────────────────────────────────────────────────────────

# 오래된 개인 글·백과사전·블로그 등 신뢰도 낮은 출처 (부분 일치)
NOISE_DOMAINS = {
    "namu.wiki", "wikipedia.org", "brunch.co.kr", "tistory.com",
    "blog.naver.com", "m.blog.naver.com", "blog.daum.net",
    "cafe.naver.com", "cafe.daum.net", "post.naver.com",
    "dcinside.com", "fmkorea.com", "clien.net", "ppomppu.co.kr",
    "youtube.com", "youtu.be", "facebook.com", "instagram.com",
    "namu.news", "wikidocs.net", "slideshare.net", "scribd.com",
}

# 신뢰 가능한 증권사·언론·금융 포털 (부분 일치) — 가점 및 우선순위
TRUSTED_DOMAINS = {
    # 증권사
    "miraeasset.com", "samsungpop.com", "kbsec.com", "nhqv.com",
    "shinhansec.com", "hanaw.com", "truefriend.com", "kiwoom.com",
    "imfnsec.com", "daishin.com", "meritz.co.kr", "ls-sec.co.kr",
    "eugenefn.com", "hanwhawm.com", "ibks.com", "db-fi.com",
    "iprovest.com", "bnkfn.co.kr", "hi-ib.com", "yuanta.co.kr",
    # 언론·포털
    "hankyung.com", "mk.co.kr", "edaily.co.kr", "fnnews.com",
    "mt.co.kr", "sedaily.com", "asiae.co.kr", "news.einfomax.co.kr",
    "yna.co.kr", "newspim.com", "wowtv.co.kr", "thebell.co.kr",
    "biz.chosun.com", "v.daum.net", "n.news.naver.com", "naver.com",
    "infostockdaily.co.kr", "paxnet.co.kr", "wisereport.co.kr",
}

# 발간일이 이 일수보다 오래되면 제외 (날짜가 추출된 경우에만 적용)
MAX_REPORT_AGE_DAYS = int(os.getenv("MAX_REPORT_AGE_DAYS", "30"))


def domain_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().lstrip("www.")
    except Exception:
        return ""


def is_noise_domain(url: str) -> bool:
    netloc = (urlparse(url).netloc or "").lower()
    return any(bad in netloc for bad in NOISE_DOMAINS)


def is_trusted_domain(url: str) -> bool:
    netloc = (urlparse(url).netloc or "").lower()
    return any(good in netloc for good in TRUSTED_DOMAINS)


def is_report_too_old(report_date: str) -> bool:
    """발간일이 추출됐고 MAX_REPORT_AGE_DAYS보다 오래됐으면 True."""
    if not report_date:
        return False  # 날짜 미상은 통과
    try:
        d = dt.date.fromisoformat(report_date)
    except ValueError:
        return False
    return (TODAY_KST - d).days > MAX_REPORT_AGE_DAYS


# ── 데이터 클래스 ────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source_query: str = ""
    source_type: str = "web"  # web | naver_news | community
    fetch_url: str = ""       # 실제 다운로드 URL. 비공개 토큰 URL은 report/state에 저장하지 않음.


@dataclass
class Candidate:
    key: str
    title: str
    url: str
    method: str
    status: str
    reason: str
    previous_method: str = ""
    stock_name: str = ""
    stock_code: str = ""
    broker: str = ""
    analyst: str = ""
    report_date: str = ""
    target_price: str = ""
    matched_terms: list[str] = field(default_factory=list)
    source_query: str = ""
    source_type: str = "web"
    # 밸류에이션 수치 (본문에서 추출되는 경우에만 채워짐)
    bps: int | None = None          # 주당순자산 (원)
    pbr: float | None = None        # 적용 PBR 배수
    eps: int | None = None          # 주당순이익 (원)
    per: float | None = None        # 적용 PER 배수
    old_price: int | None = None    # BPS × PBR 로 계산된 기존 목표가
    new_price: int | None = None    # EPS × PER 로 계산된 신규 목표가
    # 적용 기준연도 (멀티플의 EPS/BPS 기준 — 기준연도 변경 함정 방지용)
    eps_year: str = ""              # 예: 2027F, 12MF
    per_year: str = ""
    pbr_year: str = ""
    # 과거 이력 비교 결과 (같은 종목·증권사의 직전 PBR 리포트에서 복원)
    prior_method: str = ""          # 같은 증권사의 직전 산식
    prior_date: str = ""            # 직전 리포트 날짜
    prior_bps: int | None = None
    prior_pbr: float | None = None
    prior_old_price: int | None = None
    prior_target: str = ""
    # 교차 증권사 컨센서스
    consensus_brokers: list[str] = field(default_factory=list)  # 현재 PER로 본 증권사들
    # ── 빅사이클 분석 ──────────────────────────────────────────────────────────
    phase: str = ""                 # 국면 코드 ① ~ ⑤
    phase_label: str = ""           # 국면 설명 (이모지 포함)
    rerating_trend: str = ""        # 멀티플 사다리 (예: "PER 14→16배 상향")
    rerating_kind: str = ""         # 멀티플 / EPS / 혼합 / 기준연도변경
    diffusion: str = ""             # 증권사 간 확산도 (예: "최근 60일 3개사 PER 전환")
    is_cyclical: bool = False       # 시클리컬(수주산업) 여부
    valuation_timeline: list[str] = field(default_factory=list)  # 날짜별 밸류 산식 누적 요약


# ── HTTP 헬퍼 ────────────────────────────────────────────────────────────────

def request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # 레이트 리밋(429) 발생 시 지수 백오프로 최대 3회 재시도
    last_exc: Exception | None = None
    for attempt in range(3):
        response = requests.request(
            method, url,
            headers=headers, params=params, json=payload,
            timeout=25,
        )
        if response.status_code == 429:
            wait = 2 ** attempt
            print(f"[warn] 429 rate limited, retry in {wait}s", file=sys.stderr)
            time.sleep(wait)
            last_exc = requests.HTTPError("429 Too Many Requests")
            continue
        response.raise_for_status()
        return response.json()
    if last_exc:
        raise last_exc
    return {}


# ── 웹 검색 ─────────────────────────────────────────────────────────────────

def search_web(query: str, limit: int, lookback_days: int) -> list[SearchResult]:
    brave_key = os.getenv("BRAVE_SEARCH_API_KEY")
    serpapi_key = os.getenv("SERPAPI_API_KEY")
    tavily_key = os.getenv("TAVILY_API_KEY")

    if brave_key:
        freshness = "pd" if lookback_days <= 1 else "pw"
        data = request_json(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": brave_key, "User-Agent": USER_AGENT},
            params={
                "q": query, "count": min(limit, 20),
                "country": "KR", "search_lang": "ko", "freshness": freshness,
            },
        )
        return [
            SearchResult(title=item.get("title", ""), url=item.get("url", ""),
                         snippet=item.get("description", ""), source_query=query)
            for item in data.get("web", {}).get("results", []) if item.get("url")
        ]

    if serpapi_key:
        data = request_json(
            "https://serpapi.com/search.json",
            params={
                "engine": "google", "q": query, "api_key": serpapi_key,
                "google_domain": "google.co.kr", "gl": "kr", "hl": "ko",
                "num": min(limit, 10),
            },
        )
        return [
            SearchResult(title=item.get("title", ""), url=item.get("link", ""),
                         snippet=item.get("snippet", ""), source_query=query)
            for item in data.get("organic_results", []) if item.get("link")
        ]

    if tavily_key:
        time_range = "day" if lookback_days <= 1 else "week"
        # Tavily는 Authorization: Bearer 헤더 인증을 요구함 (body의 api_key는 deprecated)
        data = request_json(
            "https://api.tavily.com/search",
            method="POST",
            headers={
                "Authorization": f"Bearer {tavily_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            payload={
                "query": query,
                "search_depth": "basic",  # 무료 크레딧 절약 (1 credit/req)
                "max_results": min(limit, 10),
                "topic": "general",
                "time_range": time_range,
            },
        )
        return [
            SearchResult(title=item.get("title", ""), url=item.get("url", ""),
                         snippet=item.get("content", ""), source_query=query)
            for item in data.get("results", []) if item.get("url")
        ]

    return []


# ── 네이버 뉴스 직접 크롤 ────────────────────────────────────────────────────

def search_naver_news(query: str, lookback_days: int = 2) -> list[SearchResult]:
    """네이버 뉴스 검색 결과를 직접 크롤링합니다 (API 키 불필요)."""
    results: list[SearchResult] = []
    try:
        end_date = TODAY_KST
        start_date = end_date - dt.timedelta(days=lookback_days)
        ds = start_date.strftime("%Y.%m.%d")
        de = end_date.strftime("%Y.%m.%d")

        url = (
            "https://search.naver.com/search.naver"
            f"?where=news&query={quote_plus(query)}"
            f"&sort=1&ds={ds}&de={de}&nso=so:dd,p:from{ds.replace('.', '')}to{de.replace('.', '')}"
        )
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for item in soup.select("div.news_area")[:10]:
            title_el = item.select_one("a.news_tit")
            desc_el = item.select_one("div.news_dsc")
            if not title_el:
                continue
            results.append(SearchResult(
                title=title_el.get_text(strip=True),
                url=title_el.get("href", ""),
                snippet=desc_el.get_text(strip=True) if desc_el else "",
                source_query=query,
                source_type="naver_news",
            ))
    except Exception as exc:
        print(f"[warn] naver news search failed for '{query}': {exc}", file=sys.stderr)
    return results


def collect_naver_news(lookback_days: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    for query in NAVER_NEWS_QUERIES:
        results.extend(search_naver_news(query, lookback_days))
    return results


# ── 한경 컨센서스 (증권사 리포트 PDF 직접 수집) ────────────────────────────────

CONSENSUS_BASE = "http://consensus.hankyung.com"
CONSENSUS_LIST = CONSENSUS_BASE + "/analysis/list?skinType=business"


def collect_consensus(limit: int) -> list[SearchResult]:
    """한경 컨센서스에서 최근 증권사 기업분석 리포트 PDF 목록을 수집.

    각 행에서 제목(종목명+코드)·증권사·작성일·PDF 링크를 추출한다.
    반환되는 URL은 PDF이므로 fetch_text가 본문(산식 표 포함)을 그대로 읽는다.
    """
    results: list[SearchResult] = []
    try:
        resp = requests.get(CONSENSUS_LIST, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        tbody = soup.find("tbody")
        if not tbody:
            print("[warn] consensus: tbody not found", file=sys.stderr)
            return results

        for tr in tbody.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 6:
                continue
            link = tr.find("a", href=re.compile(r"downpdf\?report_idx="))
            if not link:
                continue
            pdf_url = CONSENSUS_BASE + link.get("href", "")
            title = link.get_text(strip=True)
            broker = tds[5].get_text(strip=True)
            date = tds[0].get_text(strip=True)
            target = tds[2].get_text(strip=True) if len(tds) > 2 else ""

            label = f"{title}"
            meta_bits = [b for b in (broker, date) if b]
            if meta_bits:
                label += f" — {' / '.join(meta_bits)}"

            results.append(SearchResult(
                title=label,
                url=pdf_url,
                snippet=f"적정가격 {target}" if target else "",
                source_query="한경컨센서스",
                source_type="broker_pdf",
            ))
    except Exception as exc:
        print(f"[warn] consensus crawl failed: {exc}", file=sys.stderr)

    return results[:limit]


# ── 부가 URL ─────────────────────────────────────────────────────────────────

def extra_url_results() -> list[SearchResult]:
    raw = os.getenv("REPORT_WATCH_URLS", "")
    urls = [part.strip() for part in re.split(r"[\n,]+", raw) if part.strip()]
    return [
        SearchResult(title=urlparse(url).netloc or url, url=url,
                     source_query="REPORT_WATCH_URLS")
        for url in urls
    ]


# ── 텔레그램 소스 수집 ───────────────────────────────────────────────────────

def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _split_env_set(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {part.strip() for part in re.split(r"[\n,]+", raw) if part.strip()}


def _telegram_message_link(msg: dict[str, Any]) -> str:
    chat = msg.get("chat", {})
    username = chat.get("username")
    message_id = msg.get("message_id")
    if username and message_id:
        return f"https://t.me/{username}/{message_id}"

    chat_id = str(chat.get("id", ""))
    if chat_id.startswith("-100") and message_id:
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    return f"telegram:{chat_id}:{message_id or ''}"


def _telegram_text_urls(text: str, entities: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for ent in entities:
        if ent.get("type") == "text_link" and ent.get("url"):
            urls.append(ent["url"])
            continue
        if ent.get("type") == "url":
            offset = ent.get("offset", 0)
            length = ent.get("length", 0)
            urls.append(text[offset:offset + length])
    urls.extend(re.findall(r"https?://[^\s<>()]+", text))
    return list(dict.fromkeys(urls))


def _telegram_file_url(bot_token: str, file_id: str) -> str:
    data = request_json(
        f"https://api.telegram.org/bot{bot_token}/getFile",
        params={"file_id": file_id},
    )
    path = data.get("result", {}).get("file_path", "")
    if not path:
        return ""
    return f"https://api.telegram.org/file/bot{bot_token}/{path}"


def _telegram_public_channel_url(channel: str) -> str:
    channel = channel.strip()
    channel = channel.removeprefix("https://t.me/")
    channel = channel.removeprefix("http://t.me/")
    channel = channel.removeprefix("t.me/")
    channel = channel.removeprefix("@")
    return f"https://t.me/s/{channel}"


def collect_telegram_public_channels() -> list[SearchResult]:
    """공개 텔레그램 채널 웹 미리보기(t.me/s/...)에서 최근 글의 URL을 수집한다.

    TELEGRAM_PUBLIC_CHANNELS=@channel1,@channel2 형태로 설정한다.
    공개 채널만 가능하며, 로그인/가입이 필요한 비공개 채널은 수집방 포워딩 방식으로 처리한다.
    """
    channels = sorted(_split_env_set("TELEGRAM_PUBLIC_CHANNELS"))
    if not channels:
        return []

    results: list[SearchResult] = []
    max_posts = int(os.getenv("TELEGRAM_PUBLIC_POSTS_LIMIT", "20"))
    for channel in channels:
        preview_url = _telegram_public_channel_url(channel)
        try:
            resp = requests.get(preview_url, headers={"User-Agent": USER_AGENT}, timeout=20)
            resp.raise_for_status()
        except Exception as exc:
            print(f"[warn] telegram public channel failed for {channel}: {exc}", file=sys.stderr)
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        for post in soup.select(".tgme_widget_message")[-max_posts:]:
            text_el = post.select_one(".tgme_widget_message_text")
            text = text_el.get_text(" ", strip=True) if text_el else ""
            if not text:
                continue

            post_url = post.get("data-post", "")
            if post_url:
                post_url = f"https://t.me/{post_url}"
            title = textwrap.shorten(text, width=90, placeholder="…")
            for link in post.select("a[href]"):
                url = link.get("href", "")
                if not url.startswith("http"):
                    continue
                # t.me 내부 이동 링크는 원문 후보로 쓰기 어렵다.
                if domain_of(url) == "t.me":
                    continue
                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=text,
                    source_query=f"텔레그램 공개채널 {channel}",
                    source_type="telegram",
                ))
            if post_url:
                results.append(SearchResult(
                    title=title,
                    url=post_url,
                    snippet=text,
                    source_query=f"텔레그램 공개채널 {channel}",
                    source_type="telegram",
                ))

    if results:
        print(f"[info] telegram public channels: {len(results)} results", file=sys.stderr)
    return results


def collect_telegram_results(state: dict[str, Any]) -> list[SearchResult]:
    """봇이 받은 텔레그램 메시지/채널 포스트에서 URL과 PDF를 수집한다.

    활성화 조건:
    - TELEGRAM_BOT_TOKEN 설정
    - TELEGRAM_COLLECT_UPDATES=1 이거나 TELEGRAM_SOURCE_CHAT_IDS 설정

    TELEGRAM_SOURCE_CHAT_IDS가 있으면 해당 chat id만 수집한다. 여러 개는 콤마/줄바꿈 구분.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    allowed_chats = _split_env_set("TELEGRAM_SOURCE_CHAT_IDS")
    if not bot_token or (not allowed_chats and not _env_bool("TELEGRAM_COLLECT_UPDATES")):
        return []

    tg_state = state.setdefault("telegram", {})
    offset = int(tg_state.get("update_offset") or 0)
    params: dict[str, Any] = {
        "timeout": 0,
        "limit": min(int(os.getenv("TELEGRAM_UPDATES_LIMIT", "50")), 100),
        "allowed_updates": json.dumps(["message", "channel_post"]),
    }
    if offset:
        params["offset"] = offset

    try:
        data = request_json(
            f"https://api.telegram.org/bot{bot_token}/getUpdates",
            params=params,
        )
    except Exception as exc:
        print(f"[warn] telegram collect failed: {exc}", file=sys.stderr)
        return []

    results: list[SearchResult] = []
    max_update_id = offset - 1
    for update in data.get("result", []):
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update_id = max(max_update_id, update_id)

        msg = update.get("channel_post") or update.get("message") or {}
        if not msg:
            continue

        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        username = str(chat.get("username", ""))
        if allowed_chats and chat_id not in allowed_chats and f"@{username}" not in allowed_chats:
            continue

        text = msg.get("text") or msg.get("caption") or ""
        entities = msg.get("entities") or msg.get("caption_entities") or []
        source_url = _telegram_message_link(msg)
        title = textwrap.shorten(text.replace("\n", " "), width=90, placeholder="…")
        if not title:
            title = msg.get("document", {}).get("file_name") or f"Telegram {chat_id}"

        for url in _telegram_text_urls(text, entities):
            results.append(SearchResult(
                title=title,
                url=url,
                snippet=text,
                source_query="텔레그램",
                source_type="telegram",
            ))

        document = msg.get("document") or {}
        file_name = document.get("file_name", "")
        mime_type = document.get("mime_type", "")
        file_id = document.get("file_id", "")
        is_pdf = mime_type == "application/pdf" or file_name.lower().endswith(".pdf")
        if file_id and is_pdf:
            fetch_url = _telegram_file_url(bot_token, file_id)
            if fetch_url:
                results.append(SearchResult(
                    title=file_name or title,
                    url=source_url,
                    snippet=text,
                    source_query="텔레그램 PDF",
                    source_type="telegram",
                    fetch_url=fetch_url,
                ))

    if max_update_id >= offset:
        tg_state["update_offset"] = max_update_id + 1
    if results:
        print(f"[info] telegram: {len(results)} results", file=sys.stderr)
    return results


# ── 텍스트 추출 ──────────────────────────────────────────────────────────────

def fetch_text(url: str) -> tuple[str, str]:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    content = response.content[:12_000_000]

    if "pdf" in content_type or url.lower().split("?")[0].endswith(".pdf"):
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages[:25]]
        return "pdf", normalize_text("\n".join(pages))

    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    body = soup.get_text("\n", strip=True)
    return "html", normalize_text(f"{title}\n{body}")


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:160_000]


# ── 패턴 분석 ────────────────────────────────────────────────────────────────

def count_patterns(text: str, patterns: list[str]) -> tuple[int, list[str]]:
    matched = []
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            matched.append(readable_pattern(pattern))
    return len(matched), matched


def readable_pattern(pattern: str) -> str:
    replacements = {
        r"PBR.{0,30}대신.{0,30}PER": "PBR 대신 PER",
        r"PBR.{0,30}에서.{0,30}PER": "PBR에서 PER",
        r"PBR.{0,40}PER.{0,20}전환": "PBR/PER 전환",
        r"PBR.{0,40}아니라.{0,30}PER": "PBR 아니라 PER",
        r"주가순자산비율.{0,30}대신.{0,30}주가수익비율": "주가순자산비율→주가수익비율",
        r"밸류에이션\s*방법.{0,30}(변경|전환)": "밸류에이션 방법 변경/전환",
        r"산정\s*방식.{0,30}(변경|전환)": "산정 방식 변경/전환",
        r"Target\s*PBR": "Target PBR",
        r"Target\s*PER": "Target PER",
        r"목표\s*PBR": "목표 PBR",
        r"목표\s*PER": "목표 PER",
        r"적정\s*PBR": "적정 PBR",
        r"적정\s*PER": "적정 PER",
        r"BPS.{0,30}PBR": "BPS x PBR",
        r"PBR.{0,30}BPS": "PBR x BPS",
        r"EPS.{0,30}PER": "EPS x PER",
        r"PER.{0,30}EPS": "PER x EPS",
        r"예상\s*BPS": "예상 BPS",
        r"예상\s*EPS": "예상 EPS",
        r"12M.{0,10}BPS": "12M BPS",
        r"12M.{0,10}EPS": "12M EPS",
    }
    return replacements.get(pattern, re.sub(r"[.{}0-9,\\]", "", pattern).strip()[:40])


def classify_method(text: str) -> tuple[str, str, list[str]]:
    switch_score, switch_terms = count_patterns(text, SWITCH_PATTERNS)
    pbr_score, pbr_terms = count_patterns(text, PBR_PATTERNS)
    per_score, per_terms = count_patterns(text, PER_PATTERNS)
    sotp_score, sotp_terms = count_patterns(text, SOTP_PATTERNS)

    terms = sorted(set(switch_terms + pbr_terms + per_terms + sotp_terms))

    if switch_score:
        return "PER", "explicit_switch", terms
    if per_score >= pbr_score + 2 and per_score:
        return "PER", "per_dominant", terms
    if pbr_score >= per_score + 2 and pbr_score:
        return "PBR", "pbr_dominant", terms
    if per_score and pbr_score:
        return "PER/PBR 병행", "mixed", terms
    if sotp_score:
        return "SOTP/EV", "sotp_or_ev", terms
    if per_score:
        return "PER", "per_signal", terms
    if pbr_score:
        return "PBR", "pbr_signal", terms
    return "미확인", "weak_signal", terms


# ── 메타 추출 ────────────────────────────────────────────────────────────────

def extract_stock(text: str, title: str) -> tuple[str, str]:
    combined = f"{title} {text[:10_000]}"
    # 종목명은 '(코드)' 바로 앞의 공백 없는 토큰 (예: 현대해상, SK하이닉스, 서부T&D).
    # 공백을 허용하면 앞쪽 URL·영문 잡음까지 빨려들어가므로 제외한다.
    matches = re.findall(r"([가-힣A-Za-z0-9&.]{2,20})\s*\((\d{6})\)", combined)
    ignore = {"KOSPI", "KOSDAQ", "BUY", "HOLD", "NR"}
    for raw_name, code in matches:
        name = raw_name.strip(" -_/|.")
        if name and name.upper() not in ignore and not name.isdigit():
            return name[:20].strip(), code

    known_scope = f"{title} {text[:600]}"
    known_matches = []
    for name in KNOWN_STOCK_NAMES:
        if name == "현대차":
            if re.search(r"현대차(?!증권)", known_scope):
                known_matches.append(name)
            continue
        if name in known_scope:
            known_matches.append(name)
    if known_matches:
        return ", ".join(known_matches[:3]), ""

    title_name = re.split(r"[:|\-_/]", title)[0].strip()
    return re.sub(r"\s+", " ", title_name)[:32], ""


def extract_broker(text: str, title: str) -> str:
    combined = f"{title} {text[:40_000]}"
    for broker in BROKERS:
        if broker in combined:
            return broker
    return ""


def extract_analyst(text: str) -> str:
    for pattern in [
        r"Analyst\s+([가-힣]{2,5})",
        r"([가-힣]{2,5})\s+[A-Za-z0-9_.+-]+@[A-Za-z0-9_.-]+",
        r"연구원\s*([가-힣]{2,5})",
    ]:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def extract_target_price(text: str) -> str:
    # 증권사마다 표현이 다름: 목표주가 / 적정주가 / 적정가치 / 적정가격 / TP
    for pattern in [
        r"목표주가\(12M\)[^\d]{0,30}([\d,]{4,})",
        r"목표주가[^\d]{0,30}([\d,]{4,})\s*원",
        r"목표가[^\d]{0,30}([\d,]{4,})\s*원",
        r"적정주가[^\d]{0,30}([\d,]{5,})",
        r"적정가치[^\d]{0,30}([\d,]{5,})",
        r"적정가격[^\d]{0,30}([\d,]{5,})",
        r"\bTP\b[^\d]{0,15}([\d,]{5,})",
    ]:
        match = re.search(pattern, text)
        if match:
            return f"{match.group(1)}원"
    return ""


def _to_int(s: str) -> int:
    return int(s.replace(",", ""))


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


def _parse_price(price_str: str) -> int | None:
    """'108,000원' → 108000"""
    if not price_str:
        return None
    m = re.search(r"([\d,]{4,})", price_str)
    return _to_int(m.group(1)) if m else None


def _within(a: int, b: int, tol: float = 0.15) -> bool:
    """a가 b의 ±tol 범위 안에 있는지 (b 기준)."""
    if not b:
        return False
    return abs(a - b) / b <= tol


def _base_year_near(text: str, span: tuple[int, int], window: int = 30) -> str:
    """매칭 위치 주변에서 기준연도 토큰을 찾는다 (2027F, 26E, 12MF, 12M Fwd 등)."""
    lo = max(0, span[0] - window)
    hi = min(len(text), span[1] + window)
    scope = text[lo:hi]
    m = re.search(r"(20\d{2}|2\d)\s*[EFP]\b", scope)       # 2027F, 26E
    if m:
        return m.group(0).replace(" ", "")
    m = re.search(r"12\s*M\s*F(?:wd)?", scope, re.IGNORECASE)  # 12MF, 12M Fwd
    if m:
        return "12MF"
    m = re.search(r"12\s*개월\s*선행", scope)
    if m:
        return "12MF"
    return ""


def extract_valuation_numbers(
    text: str,
    target_price: str = "",
) -> dict[str, Any]:
    """본문에서 BPS·PBR배수·EPS·PER배수와 적용 기준연도를 추출하고 목표가를 계산.

    반환 dict: bps, pbr, eps, per, old_price, new_price, eps_year, per_year, pbr_year

    ⚠️ 검증: PDF 표가 평면화되면 라벨과 무관한 숫자가 잡혀 엉뚱한 곱이 나올 수 있다.
    계산된 EPS×PER 값이 리포트 명시 목표주가와 ±15% 이내일 때만 신뢰한다.
    ⚠️ 기준연도: 멀티플 비교 시 'PER 14→16'이 사실은 기준연도(2026E→2027F) 변경일
    수 있으므로, EPS/PER/PBR가 어느 해 기준인지 함께 저장한다.
    """
    bps = pbr = eps = per = None
    old_price = new_price = None
    eps_year = per_year = pbr_year = ""

    # 주당 값 (원 단위, 3자리 이상) + 기준연도
    m = re.search(r"\bBPS\b\s*(?:는|은|:|=|\()?\s*([\d,]{3,})\s*원", text)
    if m:
        bps = _to_int(m.group(1))
        pbr_year = _base_year_near(text, m.span())
    m = re.search(r"\bEPS\b\s*(?:는|은|:|=|\()?\s*([\d,]{3,})\s*원", text)
    if m:
        eps = _to_int(m.group(1))
        eps_year = _base_year_near(text, m.span())

    # 배수 (PBR/PER 'N배')
    m = re.search(r"(?:Target\s*|목표\s*|적정\s*|예상\s*)?PBR\s*([\d.]{1,5})\s*배", text)
    if m:
        try:
            pbr = _to_float(m.group(1))
            if not pbr_year:
                pbr_year = _base_year_near(text, m.span())
        except ValueError:
            pbr = None
    m = re.search(r"(?:Target\s*|목표\s*|적정\s*|예상\s*)?PER\s*([\d.]{1,5})\s*배", text)
    if m:
        try:
            per = _to_float(m.group(1))
            per_year = _base_year_near(text, m.span())
        except ValueError:
            per = None

    target = _parse_price(target_price)

    # 신규(EPS×PER): 명시 목표가와 일치할 때만 신뢰
    if eps and per:
        candidate_new = round(eps * per)
        if target and _within(candidate_new, target):
            new_price = candidate_new
        else:
            eps = per = None
            eps_year = per_year = ""

    # 기존(BPS×PBR): 현재/신규 목표가와 너무 비슷하면 '기존'이 아니므로 제외
    if bps and pbr:
        candidate_old = round(bps * pbr)
        too_close_to_target = target and _within(candidate_old, target, 0.05)
        too_close_to_new = new_price and _within(candidate_old, new_price, 0.05)
        if not too_close_to_target and not too_close_to_new:
            old_price = candidate_old
        else:
            bps = pbr = None
            pbr_year = ""

    return {
        "bps": bps, "pbr": pbr, "eps": eps, "per": per,
        "old_price": old_price, "new_price": new_price,
        "eps_year": eps_year, "per_year": per_year, "pbr_year": pbr_year,
    }


def extract_report_date(text: str) -> str:
    for pattern in [
        r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})",
        r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일",
    ]:
        match = re.search(pattern, text[:30_000])
        if match:
            year, month, day = map(int, match.groups())
            try:
                return dt.date(year, month, day).isoformat()
            except ValueError:
                pass
    return ""


# ── 상태 관리 ────────────────────────────────────────────────────────────────

def state_key(stock_name: str, stock_code: str, url: str) -> str:
    if stock_code:
        return stock_code
    if stock_name:
        return hashlib.sha1(stock_name.encode("utf-8")).hexdigest()[:12]
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"stocks": {}, "seen_urls": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def decide_status(method: str, reason: str, previous_method: str) -> tuple[str, str]:
    if reason == "explicit_switch":
        return "확인", "본문에 PBR→PER 전환 표현이 감지됨"
    if previous_method.startswith("PBR") and method.startswith("PER"):
        return "확인", f"이전 방식 {previous_method} → 현재 {method}로 변경"
    if method in {"PER/PBR 병행", "SOTP/EV"}:
        return "후보", f"현재 방식이 {method}로 감지되어 전환 가능성 있음"
    if method.startswith("PER"):
        return "후보", "PER 산정 신호가 강하지만 이전 PBR 기록은 없음"
    return "관찰", f"현재 방식은 {method}"


def decide_status_v2(c: "Candidate") -> tuple[str, str]:
    """과거 이력(②)·교차 증권사(①)를 반영한 상태 판정.

    우선순위:
    1. 같은 증권사가 과거 PBR → 현재 PER  → 확인 (가장 강력, 기존 수치까지 복원됨)
    2. 본문에 명시적 전환 표현                 → 확인
    3. 종목 단위 직전 기록 PBR → 현재 PER      → 확인
    4. 복수 증권사가 PER로 수렴               → 후보(컨센서스)
    5. PER/PBR 병행·SOTP·PER 신호            → 후보
    6. 그 외                                  → 관찰
    """
    method = c.method

    # 1. 같은 증권사 과거 PBR → 현재 PER
    if c.prior_method.startswith("PBR") and method.startswith("PER"):
        date_part = f" ({c.prior_date})" if c.prior_date else ""
        return "확인", f"동일 증권사 직전 {c.prior_method}{date_part} → 현재 {method}로 전환"

    # 2. 본문 명시적 전환
    if c.reason == "explicit_switch":
        return "확인", "본문에 PBR→PER 전환 표현이 감지됨"

    # 3. 종목 단위 직전 기록 비교
    if c.previous_method.startswith("PBR") and method.startswith("PER"):
        return "확인", f"직전 방식 {c.previous_method} → 현재 {method}로 변경"

    # 4. 교차 증권사 컨센서스 (2곳 이상 PER)
    if method.startswith("PER") and len(c.consensus_brokers) >= 2:
        return "후보", f"{len(c.consensus_brokers)}개 증권사가 PER로 수렴 ({', '.join(c.consensus_brokers[:4])})"

    # 5. 병행·SOTP·PER 단일 신호
    if method in {"PER/PBR 병행", "SOTP/EV"}:
        return "후보", f"현재 방식이 {method}로 감지되어 전환 가능성 있음"
    if method.startswith("PER"):
        return "후보", "PER 산정 신호가 강하지만 이전 PBR 기록은 없음"

    return "관찰", f"현재 방식은 {method}"


# ── 후보 빌드 ────────────────────────────────────────────────────────────────

def build_candidate(result: SearchResult, text: str) -> Candidate | None:
    method, reason, matched_terms = classify_method(text)
    if method == "미확인":
        return None

    stock_name, stock_code = extract_stock(text, result.title)
    key = state_key(stock_name, stock_code, result.url)
    target_price = extract_target_price(text)
    v = extract_valuation_numbers(text, target_price)
    is_cyclical = any(kw in f"{result.title} {text[:8000]}" for kw in CYCLICAL_KEYWORDS)
    return Candidate(
        key=key,
        title=result.title.strip() or stock_name or result.url,
        url=result.url,
        method=method,
        status="관찰",
        reason=reason,
        stock_name=stock_name,
        stock_code=stock_code,
        broker=extract_broker(text, result.title),
        analyst=extract_analyst(text),
        report_date=extract_report_date(text),
        target_price=target_price,
        matched_terms=matched_terms,
        source_query=result.source_query,
        source_type=result.source_type,
        bps=v["bps"], pbr=v["pbr"], eps=v["eps"], per=v["per"],
        old_price=v["old_price"], new_price=v["new_price"],
        eps_year=v["eps_year"], per_year=v["per_year"], pbr_year=v["pbr_year"],
        is_cyclical=is_cyclical,
    )


def dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    unique: list[SearchResult] = []
    dropped_noise = 0
    for result in results:
        clean_url = result.url.split("#")[0]
        if clean_url in seen:
            continue
        # 노이즈 도메인(블로그·위키·커뮤니티 잡음)은 수집 단계에서 제외
        if is_noise_domain(clean_url):
            dropped_noise += 1
            continue
        seen.add(clean_url)
        unique.append(result)

    if dropped_noise:
        print(f"[info] noise domains dropped: {dropped_noise}", file=sys.stderr)

    # 신뢰 도메인을 앞쪽으로 정렬 (분석·발송 우선순위 확보)
    unique.sort(key=lambda r: (not is_trusted_domain(r.url), r.url))
    return unique


# ── 수집 ─────────────────────────────────────────────────────────────────────

def collect_results() -> tuple[list[SearchResult], str]:
    lookback_days = int(os.getenv("LOOKBACK_DAYS", "2"))
    limit_per_query = int(os.getenv("LIMIT_PER_QUERY", "8"))
    provider = search_provider_name()

    results: list[SearchResult] = []

    # 1) 검색 API (Brave / SerpAPI / Tavily)
    for query in QUERY_TEMPLATES:
        try:
            results.extend(search_web(query, limit_per_query, lookback_days))
        except Exception as exc:
            print(f"[warn] search failed for '{query}': {exc}", file=sys.stderr)

    # 2) 네이버 뉴스 직접 크롤
    naver_results = collect_naver_news(lookback_days)
    results.extend(naver_results)
    print(f"[info] naver news: {len(naver_results)} results", file=sys.stderr)

    # 3) 한경 컨센서스 — 증권사 리포트 PDF (산식 수치 추출용)
    consensus_limit = int(os.getenv("CONSENSUS_LIMIT", "30"))
    consensus_results = collect_consensus(consensus_limit)
    results.extend(consensus_results)
    print(f"[info] consensus PDFs: {len(consensus_results)} results", file=sys.stderr)

    # 4) 추가 URL
    results.extend(extra_url_results())

    return dedupe_results(results), provider


def search_provider_name() -> str:
    if os.getenv("BRAVE_SEARCH_API_KEY"):
        return "Brave Search API"
    if os.getenv("SERPAPI_API_KEY"):
        return "SerpAPI"
    if os.getenv("TAVILY_API_KEY"):
        return "Tavily"
    if os.getenv("REPORT_WATCH_URLS"):
        return "REPORT_WATCH_URLS only"
    return "not configured"


# ── 밸류에이션 수치 포맷 ──────────────────────────────────────────────────────

def valuation_breakdown(c: Candidate) -> str:
    """BPS×PBR → EPS×PER 수치 변경 내역을 한 줄로 포맷. 값이 없으면 빈 문자열."""
    old_part = ""
    new_part = ""

    if c.bps and c.pbr:
        old_part = f"BPS {c.bps:,}원 × PBR {c.pbr:g}배"
        if c.old_price:
            old_part += f" = {c.old_price:,}원"
    if c.eps and c.per:
        new_part = f"EPS {c.eps:,}원 × PER {c.per:g}배"
        if c.new_price:
            new_part += f" = {c.new_price:,}원"

    if old_part and new_part:
        return f"{old_part}  →  {new_part}"
    if new_part:
        return new_part
    if old_part:
        return old_part
    return ""


def _price_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value
    try:
        return f"{int(value):,}원"
    except (TypeError, ValueError):
        return str(value)


def _record_price(rec: dict[str, Any], preferred_key: str) -> str:
    for key in (preferred_key, "target"):
        text = _price_text(rec.get(key))
        if text:
            return text
    return ""


def _valuation_formula(
    base_label: str,
    base_value: int,
    multiple_label: str,
    multiple_value: float,
    price: Any = None,
) -> str:
    formula = (
        f"{base_label} {base_value:,}원 × "
        f"{multiple_label} {multiple_value:g}배"
    )
    price_text = _price_text(price)
    if price_text:
        formula += f" = {price_text}"
    return formula


def _valuation_timeline_formula(rec: dict[str, Any]) -> str:
    """이력 스냅샷 1개를 'EPS 0원 × PER 0배 = 0원' 형태로 짧게 포맷."""
    method = str(rec.get("method") or "").strip()

    if rec.get("bps") and rec.get("pbr"):
        return _valuation_formula(
            "BPS", int(rec["bps"]), "PBR", rec["pbr"],
            _record_price(rec, "old_price"),
        )
    if rec.get("eps") and rec.get("per"):
        return _valuation_formula(
            "EPS", int(rec["eps"]), "PER", rec["per"],
            _record_price(rec, "new_price"),
        )

    target = _record_price(rec, "new_price") or _record_price(rec, "old_price")
    if target:
        return f"{method or '산식미확인'} = {target}"
    return method or "산식미확인"


def build_valuation_timeline(
    brokers_hist: dict[str, Any],
    *,
    limit: int = 6,
) -> list[str]:
    """종목의 증권사별 이력을 날짜순 타임라인으로 합친다."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for broker, recs in brokers_hist.items():
        for rec in recs:
            date = str(rec.get("date") or "")
            formula = _valuation_timeline_formula(rec)
            key = (date, str(broker), formula)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "date": date or TODAY_KST.isoformat(),
                "broker": str(broker),
                "formula": formula,
            })

    rows.sort(key=lambda r: (r["date"], r["broker"], r["formula"]))
    clipped = rows[-limit:]
    timeline = [
        f"{row['broker']} {row['date']} {row['formula']}".strip()
        for row in clipped
    ]
    hidden = len(rows) - len(clipped)
    if hidden > 0:
        timeline.insert(0, f"… 이전 {hidden}건")
    return timeline


# ── 텔레그램 발송 ────────────────────────────────────────────────────────────

def tg_escape(text: str) -> str:
    """텔레그램 HTML 모드용 특수문자 이스케이프."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def dedupe_by_stock(candidates: list[Candidate]) -> list[Candidate]:
    """같은 종목은 첫 번째(신뢰도 높은) 항목만 남김."""
    seen: set[str] = set()
    unique: list[Candidate] = []
    for c in candidates:
        name = c.stock_name or c.title
        if name in seen:
            continue
        seen.add(name)
        unique.append(c)
    return unique


def _phase_badge(c: Candidate) -> str:
    """국면 + 시클리컬 배지 (예: '🔄 전환(골든존) · 시클리컬')."""
    bits = []
    if c.phase_label:
        bits.append(c.phase_label)
    if c.is_cyclical:
        bits.append("시클리컬")
    return " · ".join(bits)


def tg_line(c: Candidate) -> str:
    """후보용 한 줄(+수치) 요약: • <a>종목</a> — 증권사 · 목표가"""
    name = (c.stock_name or c.title)[:24]
    bits = []
    if c.broker:
        bits.append(c.broker)
    if c.target_price:
        bits.append(c.target_price)
    suffix = f" — {' · '.join(bits)}" if bits else ""
    line = f"• <a href='{c.url}'>{tg_escape(name)}</a>{tg_escape(suffix)}"

    badge = _phase_badge(c)
    if badge:
        line += f"\n   ↳ {tg_escape(badge)}"
    if c.rerating_trend:
        line += f"\n   ↳ {tg_escape(c.rerating_trend)}"
    else:
        breakdown = valuation_breakdown(c)
        if breakdown:
            line += f"\n   ↳ {tg_escape(breakdown)}"
    return line


def _timeline_row_date(row: str) -> str:
    match = re.search(r"\d{4}-\d{2}-\d{2}", row)
    return match.group(0) if match else ""


def tg_confirmed_line(c: Candidate) -> str:
    """확인된 전환만 종목별 시계열 산식으로 짧게 포맷."""
    name = (c.stock_name or c.title)[:24]
    lines = [f"• <a href='{c.url}'>{tg_escape(name)}</a>"]

    rows = [row for row in c.valuation_timeline if not row.startswith("…")]

    if c.prior_bps and c.prior_pbr:
        old_prefix = " ".join(part for part in (c.broker, c.prior_date) if part)
        old_formula = _valuation_formula(
            "BPS", c.prior_bps, "PBR", c.prior_pbr,
            c.prior_old_price or c.prior_target,
        )
        old_row = f"{old_prefix} {old_formula}".strip()
        if old_row and old_row not in rows:
            rows.append(old_row)
    elif c.prior_method and c.prior_target:
        old_prefix = " ".join(part for part in (c.broker, c.prior_date) if part)
        old_row = f"{old_prefix} {c.prior_method} = {c.prior_target}".strip()
        if old_row and old_row not in rows:
            rows.append(old_row)

    if c.eps and c.per:
        new_prefix = " ".join(part for part in (c.broker, c.report_date) if part)
        new_formula = _valuation_formula(
            "EPS", c.eps, "PER", c.per, c.new_price or c.target_price,
        )
        new_row = f"{new_prefix} {new_formula}".strip()
        if new_row and new_row not in rows:
            rows.append(new_row)
    elif c.target_price:
        new_prefix = " ".join(part for part in (c.broker, c.report_date) if part)
        new_row = f"{new_prefix} {c.method or '산식미확인'} = {c.target_price}".strip()
        if new_row and new_row not in rows:
            rows.append(new_row)

    rows.sort(key=lambda row: (_timeline_row_date(row), row))
    lines.extend(f"   ㄴ {tg_escape(row)}" for row in rows[:8])

    return "\n".join(lines)


def send_telegram(bot_token: str, chat_id: str, candidates: list[Candidate]) -> None:
    confirmed = dedupe_by_stock([c for c in candidates if c.status == "확인"])

    if not confirmed:
        return

    lines = [
        f"📊 <b>밸류에이션 전환 — {TODAY_KST.isoformat()}</b>",
        f"확인 {len(confirmed)}",
        "\n🔴 <b>확인된 전환</b>",
    ]

    shown = confirmed[:8]
    lines.extend(tg_confirmed_line(c) for c in shown)
    more = len(confirmed) - len(shown)
    if more > 0:
        lines.append(f"… 외 확인 {more}건")

    lines.append(
        "\n📄 <a href='https://github.com/helloblackfinger/"
        f"valuation-method-watch/blob/main/reports/{TODAY_KST.isoformat()}.md'>전체 리포트</a>"
    )

    text = "\n".join(lines)

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        resp.raise_for_status()
        print(f"[info] telegram sent: {resp.json().get('ok')}", file=sys.stderr)
    except Exception as exc:
        print(f"[warn] telegram send failed: {exc}", file=sys.stderr)


# ── AI 요약 ──────────────────────────────────────────────────────────────────

def openai_summary(candidates: list[Candidate]) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not candidates:
        return ""

    model = os.getenv("OPENAI_MODEL") or "gpt-4.1-mini"
    compact = [
        {
            "status": c.status, "stock": c.stock_name, "code": c.stock_code,
            "broker": c.broker, "method": c.method,
            "previous_method": c.previous_method,
            "target_price": c.target_price,
            "terms": c.matched_terms[:8], "url": c.url,
        }
        for c in candidates[:30]
    ]
    prompt = (
        "아래는 국내 증권사 리포트/기사의 밸류에이션 산식 감지 결과입니다. "
        "확인된 전환과 후보를 한국어로 짧게 요약하세요. 투자 권유 문구는 쓰지 마세요.\n\n"
        f"{json.dumps(compact, ensure_ascii=False)}"
    )
    try:
        data = request_json(
            "https://api.openai.com/v1/responses",
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            payload={"model": model, "input": prompt, "max_output_tokens": 700},
        )
    except Exception as exc:
        print(f"[warn] OpenAI summary failed: {exc}", file=sys.stderr)
        return ""

    if data.get("output_text"):
        return data["output_text"].strip()

    parts = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                parts.append(content.get("text", ""))
    return "\n".join(parts).strip()


# ── 리포트 렌더 ──────────────────────────────────────────────────────────────

def render_candidate(c: Candidate) -> str:
    identity = c.stock_name or c.title
    if c.stock_code:
        identity += f" ({c.stock_code})"
    source_label = {
        "naver_news": "네이버뉴스",
        "community": "커뮤니티",
        "broker_pdf": "증권사PDF",
        "telegram": "텔레그램",
    }.get(c.source_type, "웹")
    lines = [
        f"### {escape_md(identity)}",
        f"- 상태: {c.status}",
        f"- 현재 감지 산식: {c.method}",
        f"- 출처 유형: {source_label}",
    ]
    if c.phase_label:
        lines.append(f"- 빅사이클 국면: {c.phase} {escape_md(c.phase_label)}{' · 시클리컬' if c.is_cyclical else ''}")
    if c.previous_method:
        lines.append(f"- 이전 감지 산식: {c.previous_method}")
    if c.broker:
        lines.append(f"- 증권사: {escape_md(c.broker)}")
    if c.analyst:
        lines.append(f"- 애널리스트: {escape_md(c.analyst)}")
    if c.report_date:
        lines.append(f"- 발간일/감지일: {c.report_date}")
    if c.target_price:
        lines.append(f"- 목표가: {escape_md(c.target_price)}")
    if c.valuation_timeline:
        lines.append("- 밸류 타임라인:")
        lines.extend(f"  - {escape_md(row)}" for row in c.valuation_timeline)
    breakdown = valuation_breakdown(c)
    if breakdown:
        lines.append(f"- 산식 수치: {escape_md(breakdown)}")
    # 과거 이력에서 복원한 기존 산식 (현재 리포트에 없을 때)
    if not (c.bps and c.pbr) and c.prior_bps and c.prior_pbr:
        old = f"BPS {c.prior_bps:,}원 × PBR {c.prior_pbr:g}배"
        if c.prior_old_price:
            old += f" = {c.prior_old_price:,}원"
        lines.append(f"- 기존 산식(과거 {c.prior_date}): {escape_md(old)}")
    elif c.prior_method and c.prior_target:
        lines.append(f"- 기존 산식(과거 {c.prior_date}): {escape_md(c.prior_method)} 방식, 목표가 {escape_md(c.prior_target)}")
    if c.rerating_trend:
        kind = f" ({c.rerating_kind})" if c.rerating_kind else ""
        lines.append(f"- 멀티플 재평가: {escape_md(c.rerating_trend + kind)}")
    if c.diffusion:
        lines.append(f"- 확산도: {escape_md(c.diffusion)}")
    if len(c.consensus_brokers) >= 2:
        lines.append(f"- 증권사 합의: {len(c.consensus_brokers)}곳 PER 적용 ({escape_md(', '.join(c.consensus_brokers[:6]))})")
    lines.append(f"- 전환 판단: {escape_md(c.reason)}")
    if c.matched_terms:
        lines.append(f"- 감지 단서: {escape_md(', '.join(c.matched_terms[:10]))}")
    lines.append(f"- 출처: [{escape_md(c.title[:80])}]({c.url})")
    return "\n".join(lines)


def escape_md(text: str) -> str:
    return text.replace("\n", " ").strip()


def render_report(
    candidates: list[Candidate],
    provider: str,
    fetched_count: int,
    failed_urls: list[str],
    naver_news_count: int,
    consensus_count: int = 0,
    telegram_count: int = 0,
) -> str:
    confirmed = [c for c in candidates if c.status == "확인"]
    watch = [c for c in candidates if c.status == "후보"]
    observed = [c for c in candidates if c.status == "관찰"]
    ai = openai_summary(confirmed + watch)

    parts = [
        f"# 국내 밸류에이션 전환 감시 — {TODAY_KST.isoformat()}",
        "",
        "목적: 목표주가 산정 방식이 `BPS x PBR` → `EPS x PER`로 바뀌는 종목 또는 SOTP/EV 전환 종목을 포착합니다.",
        "",
        f"- 검색 엔진: {provider}",
        f"- 네이버 뉴스 직접 수집: {naver_news_count}건",
        f"- 한경 컨센서스 PDF: {consensus_count}건",
        f"- 텔레그램 수집: {telegram_count}건",
        f"- 확인한 URL 수: {fetched_count}",
        f"- 확인된 전환: {len(confirmed)}",
        f"- 후보: {len(watch)}",
        "",
    ]

    if ai:
        parts.extend(["## AI 요약", "", ai, ""])

    parts.extend(["## 확인된 전환", ""])
    if confirmed:
        parts.append("\n\n".join(render_candidate(c) for c in confirmed))
    else:
        parts.append("오늘 공개 접근 범위에서는 명확한 산식 전환을 찾지 못했습니다.")
    parts.append("")

    parts.extend(["## 후보", ""])
    if watch:
        parts.append("\n\n".join(render_candidate(c) for c in watch[:20]))
    else:
        parts.append("후보로 분류할 만한 신규 감지 항목이 없습니다.")
    parts.append("")

    parts.extend(["## 관찰 항목", ""])
    if observed:
        parts.append("\n\n".join(render_candidate(c) for c in observed[:20]))
    else:
        parts.append("관찰 항목이 없습니다.")
    parts.append("")

    parts.extend(["## 제한 사항", ""])
    parts.append("- 로그인·유료·권한 제한 리포트는 우회하지 않습니다.")
    parts.append("- 이미지형 PDF는 텍스트 추출이 누락될 수 있습니다.")
    parts.append("- GitHub Actions schedule은 GitHub 상태에 따라 지연될 수 있습니다.")
    if failed_urls:
        parts.append(f"- 가져오기 실패 URL 수: {len(failed_urls)}")
    if provider == "not configured":
        parts.append("- 검색 API secret이 설정되지 않아 자동 검색을 수행하지 못했습니다.")

    return "\n".join(parts).strip() + "\n"


def _snapshot(c: Candidate, date: str) -> dict[str, Any]:
    """이력에 저장할 리포트 스냅샷."""
    return {
        "date": date or TODAY_KST.isoformat(),
        "method": c.method,
        "bps": c.bps, "pbr": c.pbr,
        "eps": c.eps, "per": c.per,
        "old_price": c.old_price, "new_price": c.new_price,
        "eps_year": c.eps_year, "per_year": c.per_year, "pbr_year": c.pbr_year,
        "target": c.target_price,
        "url": c.url,
    }


def _days_between(d1: str, d2: str) -> int | None:
    try:
        return abs((dt.date.fromisoformat(d1) - dt.date.fromisoformat(d2)).days)
    except Exception:
        return None


def detect_rerating(candidate: Candidate, past: list[dict[str, Any]]) -> None:
    """A. 같은 증권사의 직전 동일-방식 리포트와 비교해 멀티플 재평가(re-rating) 감지.

    핵심 주의: 'PER 14→16'이 기준연도(2026E→2027F) 변경 때문일 수 있으므로,
    기준연도가 같을 때만 '순수 멀티플 상향'으로 본다.
    """
    method = candidate.method
    if not past:
        return

    if method.startswith("PER") and candidate.per:
        prior = next(
            (r for r in sorted(past, key=lambda r: r.get("date", ""), reverse=True)
             if str(r.get("method", "")).startswith("PER") and r.get("per")),
            None,
        )
        if prior and candidate.per > prior["per"]:
            same_year = (
                candidate.per_year and prior.get("per_year")
                and candidate.per_year == prior.get("per_year")
            )
            candidate.rerating_trend = f"PER {prior['per']:g}→{candidate.per:g}배 상향"
            if prior.get("per_year") and candidate.per_year and not same_year:
                candidate.rerating_kind = "기준연도변경"  # 진짜 re-rating 아닐 수 있음
                candidate.rerating_trend += f" (기준 {prior.get('per_year')}→{candidate.per_year})"
            else:
                # EPS 변화와 비교해 멀티플/실적 분해
                if prior.get("eps") and candidate.eps and candidate.eps > prior["eps"] * 1.03:
                    candidate.rerating_kind = "혼합"  # 멀티플↑ + EPS↑
                else:
                    candidate.rerating_kind = "멀티플"  # 순수 재평가

    elif method.startswith("PBR") and candidate.pbr:
        prior = next(
            (r for r in sorted(past, key=lambda r: r.get("date", ""), reverse=True)
             if str(r.get("method", "")).startswith("PBR") and r.get("pbr")),
            None,
        )
        if prior and candidate.pbr > prior["pbr"]:
            candidate.rerating_trend = f"PBR {prior['pbr']:g}→{candidate.pbr:g}배 상향"
            candidate.rerating_kind = "멀티플"


def count_multiple_raises(records: list[dict[str, Any]], field_name: str) -> int:
    """이력에서 해당 배수(pbr/per)가 연속 상향된 횟수."""
    vals = [r.get(field_name) for r in sorted(records, key=lambda r: r.get("date", "")) if r.get(field_name)]
    raises = 0
    for a, b in zip(vals, vals[1:]):
        if b > a:
            raises += 1
    return raises


def classify_phase(candidate: Candidate, brokers_hist: dict[str, Any]) -> tuple[str, str]:
    """B. 빅사이클 국면 ①~⑤ 분류.

    ① 턴어라운드   : BPS×PBR, PBR≈1.0, 상향 이력 없음
    ② PBR 재평가   : BPS×PBR, Target PBR 상향 진행
    ③ 전환(골든존) : PBR → PER/SOTP/EV 로 방식 전환
    ④ PER 재평가   : EPS×PER, Target PER 상향 진행
    ⑤ 후기         : PER 고배수(≥20) 또는 EPS 정체 속 PER만 상승
    """
    method = candidate.method
    today = TODAY_KST.isoformat()
    # 종목 전체(모든 증권사) 기록 합산 (오늘 기록 제외)
    all_recs: list[dict[str, Any]] = []
    for recs in brokers_hist.values():
        all_recs.extend(r for r in recs if r.get("date") != today)

    pbr_raises = count_multiple_raises(all_recs, "pbr")
    per_raises = count_multiple_raises(all_recs, "per")

    # '전환 직후(골든존)'는 직전 리포트가 PBR이었을 때만 — 같은 증권사 우선
    broker_recs = [r for r in brokers_hist.get(candidate.broker, []) if r.get("date") != today]
    ref_recs = broker_recs or all_recs
    last_method = ""
    if ref_recs:
        last_method = str(max(ref_recs, key=lambda r: r.get("date", "")).get("method", ""))
    switched = last_method.startswith("PBR") and method.startswith("PER")

    if method.startswith("PER"):
        if switched:
            return "③", "🔄 전환(골든존)"
        if candidate.per and candidate.per >= 20:
            return "⑤", "⚠️ 후기(고멀티플)"
        if per_raises >= 2:
            return "④", "🚀 PER 재평가"
        return "④", "🚀 PER 적용"
    if method.startswith("PBR"):
        if pbr_raises >= 2 or (candidate.pbr and candidate.pbr >= 1.5):
            return "②", "📈 PBR 재평가"
        if candidate.pbr and candidate.pbr <= 1.2:
            return "①", "🌱 턴어라운드"
        return "②", "📈 PBR 구간"
    if method in {"PER/PBR 병행", "SOTP/EV"}:
        return "③", "🔄 전환 가능"
    return "", ""


def compute_diffusion(candidate: Candidate, brokers_hist: dict[str, Any], window_days: int = 60) -> str:
    """E. 증권사 간 확산도 — 최근 window_days 내 PER로 이동/상향한 증권사 수."""
    today = TODAY_KST.isoformat()
    per_recent: set[str] = set()
    raised_recent: set[str] = set()
    for bname, recs in brokers_hist.items():
        for r in recs:
            d = r.get("date", "")
            within = _days_between(d, today)
            if within is not None and within <= window_days:
                if str(r.get("method", "")).startswith("PER"):
                    per_recent.add(bname)
    if candidate.broker and candidate.method.startswith("PER"):
        per_recent.add(candidate.broker)
    # 멀티플 상향 증권사
    for bname, recs in brokers_hist.items():
        if count_multiple_raises(
            [r for r in recs if _days_between(r.get("date", ""), today) is not None
             and _days_between(r.get("date", ""), today) <= window_days],
            "per",
        ) >= 1:
            raised_recent.add(bname)

    bits = []
    if len(per_recent) >= 2:
        bits.append(f"최근 {window_days}일 {len(per_recent)}개사 PER 적용")
    if len(raised_recent) >= 2:
        bits.append(f"{len(raised_recent)}개사 PER 상향")
    return " · ".join(bits)


def update_candidates_with_state(candidates: list[Candidate], state: dict[str, Any]) -> None:
    """과거 이력 비교(②) + 교차 증권사 종합(①)을 수행하고 상태를 갱신한다.

    state["history"][stock_key] = {
        "stock_name": ...,
        "brokers": { "<증권사>": [snapshot, ...] }
    }
    """
    stocks = state.setdefault("stocks", {})
    history = state.setdefault("history", {})
    now = dt.datetime.now(KST).isoformat(timespec="seconds")
    today = TODAY_KST.isoformat()

    for candidate in candidates:
        previous = stocks.get(candidate.key, {})
        candidate.previous_method = previous.get("method", "")

        stock_hist = history.setdefault(
            candidate.key, {"stock_name": candidate.stock_name, "brokers": {}}
        )
        stock_hist["stock_name"] = candidate.stock_name or stock_hist.get("stock_name", "")
        brokers_hist: dict[str, Any] = stock_hist.setdefault("brokers", {})

        # ── ② 같은 증권사의 직전 리포트와 비교 ──────────────────────────────
        past: list[dict[str, Any]] = []
        if candidate.broker:
            past = [
                r for r in brokers_hist.get(candidate.broker, [])
                if r.get("date") != today  # 오늘 동일 리포트는 제외
            ]
            # 가장 최근의 'PBR 계열' 리포트를 찾아 기존 산식 복원
            for rec in sorted(past, key=lambda r: r.get("date", ""), reverse=True):
                if str(rec.get("method", "")).startswith("PBR"):
                    candidate.prior_method = rec.get("method", "")
                    candidate.prior_date = rec.get("date", "")
                    candidate.prior_bps = rec.get("bps")
                    candidate.prior_pbr = rec.get("pbr")
                    candidate.prior_old_price = rec.get("old_price")
                    candidate.prior_target = rec.get("target", "")
                    break

        # ── A. 멀티플 재평가(re-rating) 감지 (기준연도 함정 보정 포함) ────────
        detect_rerating(candidate, past)

        # ── ① 교차 증권사 컨센서스: 이 종목을 PER로 보는 증권사 집합 ─────────
        per_brokers: set[str] = set()
        for bname, recs in brokers_hist.items():
            latest = max(recs, key=lambda r: r.get("date", ""), default=None) if recs else None
            if latest and str(latest.get("method", "")).startswith("PER"):
                per_brokers.add(bname)
        if candidate.broker and candidate.method.startswith("PER"):
            per_brokers.add(candidate.broker)
        candidate.consensus_brokers = sorted(per_brokers)

        # ── B. 국면 분류 + E. 증권사 간 확산도 ──────────────────────────────
        candidate.phase, candidate.phase_label = classify_phase(candidate, brokers_hist)
        candidate.diffusion = compute_diffusion(candidate, brokers_hist)

        # ── 상태 결정 (이력 반영) ────────────────────────────────────────────
        candidate.status, candidate.reason = decide_status_v2(candidate)

        # ── 오늘 스냅샷을 이력에 기록 (증권사별) ─────────────────────────────
        if candidate.broker:
            recs = brokers_hist.setdefault(candidate.broker, [])
            recs[:] = [r for r in recs if r.get("date") != today]  # 같은 날 중복 제거
            recs.append(_snapshot(candidate, candidate.report_date))
            recs.sort(key=lambda r: r.get("date", ""))
            del recs[:-12]  # 증권사당 최근 12건만 유지

        candidate.valuation_timeline = build_valuation_timeline(brokers_hist)

        stocks[candidate.key] = {
            "stock_name": candidate.stock_name,
            "stock_code": candidate.stock_code,
            "method": candidate.method,
            "title": candidate.title,
            "url": candidate.url,
            "broker": candidate.broker,
            "target_price": candidate.target_price,
            "last_seen_at": now,
        }


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()
    results, provider = collect_results()
    telegram_results = collect_telegram_results(state)
    telegram_results.extend(collect_telegram_public_channels())
    if telegram_results:
        results = dedupe_results(results + telegram_results)

    naver_news_count = sum(1 for r in results if r.source_type == "naver_news")
    consensus_count = sum(1 for r in results if r.source_type == "broker_pdf")
    telegram_count = sum(1 for r in results if r.source_type == "telegram")

    candidates: list[Candidate] = []
    failed_urls: list[str] = []
    fetched_count = 0
    dropped_old = 0

    for result in results:
        try:
            _, text = fetch_text(result.fetch_url or result.url)
            fetched_count += 1
        except Exception as exc:
            print(f"[warn] fetch failed for {result.url}: {exc}", file=sys.stderr)
            failed_urls.append(result.url)
            text = normalize_text(f"{result.title} {result.snippet}")

        candidate = build_candidate(result, text)
        if candidate:
            # 발간일이 너무 오래된 글(예: 수년 전 블로그)은 제외
            if is_report_too_old(candidate.report_date):
                dropped_old += 1
                continue
            candidate.source_type = result.source_type
            candidates.append(candidate)

    if dropped_old:
        print(f"[info] old reports dropped (>{MAX_REPORT_AGE_DAYS}d): {dropped_old}", file=sys.stderr)

    update_candidates_with_state(candidates, state)
    state["last_run_at"] = dt.datetime.now(KST).isoformat(timespec="seconds")
    save_state(state)

    # 리포트 저장
    report = render_report(
        candidates, provider, fetched_count, failed_urls,
        naver_news_count, consensus_count, telegram_count,
    )
    report_path = REPORTS_DIR / f"{TODAY_KST.isoformat()}.md"
    report_path.write_text(report, encoding="utf-8")

    # 텔레그램 발송
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if tg_token and tg_chat:
        send_telegram(tg_token, tg_chat, candidates)
    else:
        print("[info] telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정)", file=sys.stderr)

    confirmed = sum(1 for c in candidates if c.status == "확인")
    watch = sum(1 for c in candidates if c.status == "후보")
    print(textwrap.dedent(f"""
        Wrote {report_path}
        Provider:      {provider}
        Naver news:    {naver_news_count}
        Consensus PDF: {consensus_count}
        Telegram:      {telegram_count}
        Results:       {len(results)}
        Fetched:       {fetched_count}
        Candidates:    {len(candidates)} (확인 {confirmed} / 후보 {watch})
    """).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
