# API 키 발급 가이드

세 곳에서 자격증명을 받아 `c:\Research\stock-dashboard\.env` 에 채웁니다.
`.env` 는 `.gitignore` 에 등록되어 있어 커밋되지 않습니다.

---

## 1. FRED (미국 매크로) — 무료, 즉시 발급

연준 총자산·TGA·역레포·미국 M2·금리 등에 사용.

1. https://fredaccount.stlouisfed.org/login/secure/ 에서 계정 생성
   (이메일 인증 링크 클릭 필요)
2. 로그인 후 https://fredaccount.stlouisfed.org/apikeys 접속
3. **"Request API Key"** 클릭 → 용도란에 아무거나 (예: `personal research dashboard`)
4. 32자리 소문자+숫자 문자열이 즉시 발급됨

```
FRED_API_KEY=abcdef0123456789abcdef0123456789
```

---

## 2. ECOS (한국은행 경제통계) — 무료, 즉시 발급

한국 M2·기준금리·투자자예탁금·원달러 환율에 사용.

1. https://ecos.bok.or.kr/api/#/AuthKeyApply 접속
2. 이메일 주소, 기관/용도 입력 후 신청
3. **인증키가 입력한 이메일로 즉시 발송**됨 (승인 대기 없음)

```
ECOS_API_KEY=ABCD1234EFGH5678IJKL
```

> 주의: ECOS는 하루 호출 한도가 있습니다. Phase 1 탐색 시 `1/1000` 범위로 제한해 호출하세요.

---

## 3. KRX (data.krx.co.kr) — 무료 회원가입, ★ 새로 필요해진 항목

**CLAUDE.md 작성 시점 이후 pykrx 가 바뀌었습니다.** 설치된 pykrx 1.2.8 은
`data.krx.co.kr` 로그인 세션 없이는 어떤 데이터도 반환하지 않습니다.
(`pykrx/website/comm/auth.py` 가 `KRX_ID` / `KRX_PW` 환경변수를 읽어 로그인합니다.)

1. http://data.krx.co.kr 접속 → 우측 상단 **회원가입**
2. 개인회원 가입 (본인인증 필요, 무료)
3. 가입한 아이디/비밀번호를 `.env` 에 기록

```
KRX_ID=myuserid
KRX_PW=mypassword
```

### 이게 없으면 무엇을 못 하나

| 데이터 | pykrx 없이 대안 | 비고 |
|---|---|---|
| KOSPI/KOSDAQ/KOSPI200 OHLCV | ✅ FinanceDataReader (`KS11`/`KQ11`/`KS200`) | 로그인 불필요. 시가총액·거래대금까지 제공 |
| 투자자별 수급 (개인/외국인/기관) | ❌ 대안 없음 | `flows.parquet` 및 수급 차트 전체가 비게 됨 |

즉 **수급 데이터를 포기할 게 아니라면 KRX 가입이 필요**합니다.

---

## 4. 확인

```bash
cd c:/Research/stock-dashboard
.venv/Scripts/python.exe notebooks/00_explore_api.py
```

4개 소스 모두 실제 데이터가 출력되면 Phase 1 DoD 통과입니다.

---

## GitHub Actions 배포 시 (Phase 5)

동일한 이름으로 Repository Secrets 에 등록합니다:
`FRED_API_KEY`, `ECOS_API_KEY`, `KRX_ID`, `KRX_PW`
