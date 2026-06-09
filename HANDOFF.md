# 밸류에이션 전환 감시 봇 — 작업 핸드오프

> 마지막 업데이트: 2026-06-04 (KST)
> 레포: https://github.com/helloblackfinger/valuation-method-watch

## 한 줄 요약

국내 증권사 리포트·뉴스·PDF를 매일 자동 수집해, **목표주가 산정 방식이 `BPS×PBR`에서 `EPS×PER`로 바뀌는 종목**과 **멀티플 재평가(re-rating) 사다리**를 추적해 텔레그램으로 알려주는 봇. "전환 알람"을 넘어 **"re-rating ladder 추적기"**로 진화함.

## 핵심 아이디어 (빅사이클 가치평가 법칙)

```
주가 = EPS×PER  또는  BPS×PBR
턴어라운드(실적 X) → BPS×PBR 로 시작
실적 나오기 시작 → EPS×PER 로 전환
실적 성장 → PER 배수가 계속 상향 (12→14→16→20→26)
```
이 사다리(①~⑤)를 다 먹으면 2배·3배·10배. 대부분은 ②에서 팔아버림.
실제 검증 사례: HD현대일렉트릭 (PBR 1.0→2.5 → PER 12→20, 3년 25배).

## 국면 분류 ①~⑤ (classify_phase)

| 국면 | 조건 | 라벨 |
|------|------|------|
| ① 턴어라운드 | BPS×PBR, PBR≤1.2, 상향이력 없음 | 🌱 |
| ② PBR 재평가 | BPS×PBR, PBR 상향 진행(≥1.5 또는 2회+) | 📈 |
| ③ 전환(골든존) | 직전 PBR → 현재 PER로 방식 전환 | 🔄 |
| ④ PER 재평가 | EPS×PER, PER 상향 진행 | 🚀 |
| ⑤ 후기 | PER≥20 고배수 | ⚠️ |

## 구현된 기능 (작업 순서대로)

1. **데이터 소스**: Tavily 웹검색 + 한경 컨센서스 증권사 PDF 크롤 + 네이버뉴스(GH IP 차단됨) + 텔레그램 봇 수신 메시지/PDF
2. **텔레그램 발송**: BOT_TOKEN + CHAT_ID (봇 API 방식)
3. **오탐 제거**: 노이즈 도메인(블로그·위키·커뮤니티) 차단, 신뢰 도메인 우선, 30일 이내
4. **산식 수치 추출**: EPS×PER / BPS×PBR 금액 계산, **목표가 ±15% 교차검증**(거짓 숫자 폐기)
5. **목표가 추출 확대**: 목표주가/적정주가/적정가치/적정가격/TP
6. **과거 이력 비교(②)**: (종목×증권사×날짜) history 누적 → 동일 증권사 PBR→PER 전환 시 "확인" 승격 + 기존 BPS×PBR 수치 복원
7. **교차 증권사 종합(①)**: 복수 증권사 PER 수렴 → 컨센서스
8. **확인 종목 팔로업**: `confirmed_transitions` 레지스트리에 한 번 확인된 종목을 보존 → 텔레그램은 후보 없이 확인 종목의 증권사별 밸류 이력만 표시
9. **빅사이클 엔진(A+B+E+C)**:
   - A. 멀티플 재평가 감지 (PER/PBR 배수 상향) + **기준연도(2026E→2027F) 함정 보정**
   - B. 국면 ①~⑤ 자동 분류
   - E. 증권사 간 확산도 (최근 60일 PER 전환·상향 증권사 수)
   - C. 시클리컬(수주산업) 키워드 태깅
10. **종목명 정리**: '(코드)' 앞 공백 없는 토큰만 추출, pykrx로 정식명 교체

## 파일 구조

- `scripts/valuation_method_watch.py` — 전체 로직 (단일 파일)
- `.github/workflows/daily-valuation-watch.yml` — 매일 06:00 KST cron
- `state/valuation_methods.json` — 이력 저장 (history[종목코드][brokers][증권사]=[스냅샷])
- `reports/YYYY-MM-DD.md` — 일별 리포트 (KST 날짜 기준)

## GitHub Secrets / Variables (설정됨)

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (= 1459739042)
- `TAVILY_API_KEY` (무료, Bearer 인증)
- env: LOOKBACK_DAYS=2, MAX_REPORT_AGE_DAYS=30, CONSENSUS_LIMIT=30
- optional vars: TELEGRAM_COLLECT_UPDATES=1, TELEGRAM_SOURCE_CHAT_IDS, TELEGRAM_UPDATES_LIMIT, TELEGRAM_PUBLIC_CHANNELS, TELEGRAM_PUBLIC_POSTS_LIMIT

## 현재 상태 (2026-06-04)

- ✅ 매일 자동 실행 정상, 텔레그램 발송 정상
- ✅ 국면 분류 작동 (27종목 분류, 시클리컬 18건)
- ⏳ **멀티플 재평가 = 0건** (정상): 기준연도 저장 첫날이라 비교할 과거 배수 없음.
  며칠~몇 주 history 쌓이면 "PER 14→16배 상향" 사다리가 잡히기 시작함.

## 알려진 한계 / 다음 후보

- 네이버뉴스: GitHub Actions IP 차단으로 0건 (Tavily가 커버)
- 일부 해외종목·뉴스 헤드라인은 종목코드 미식별 → history 추적 불가 (필터링됨)
- D. EPS 추세/수주잔고 연동: 리포트 본문에 묻혀 자동 수집 까다로움 (미구현, 보조신호 후보)
- 진화 후보: 특정 섹터(조선 등) 집중 모드, EPS 기울기 추적, 국면 전환 알림 강화
- 텔레그램 수집: 수집방 모드(Bot API updates) + 공개채널 모드(t.me/s 웹 미리보기). 비공개/유료 채널 글은 수집용 그룹/채널로 포워딩하는 방식이 가장 안정적.

## 이어서 작업할 때

1. 코드: `git clone` 후 `scripts/valuation_method_watch.py` 단일 파일 수정
2. 로컬 테스트: Python 3.12 권장 (3.9는 importlib+dataclass 이슈 → sys.modules 등록 우회)
3. 수동 실행: `gh workflow run daily-valuation-watch.yml`
4. 결과 확인: `gh run watch <id>` → `reports/`, `state/` 풀
