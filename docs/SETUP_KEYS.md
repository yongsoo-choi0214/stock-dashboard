# API 키 받는 법 — 처음부터

## 0. 그전에: 키가 뭐고 어디에 넣나

API 키는 **"이 데이터를 요청하는 게 나다"라고 밝히는 비밀번호 같은 문자열**입니다.
기관이 무료로 데이터를 열어주되, 누가 얼마나 쓰는지는 알고 싶어서 요구합니다.

넣는 곳은 딱 한 군데입니다:

```
c:\Research\stock-dashboard\.env
```

이 파일은 이미 만들어져 있고, 지금은 등호 뒤가 비어 있습니다:

```
FRED_API_KEY=
ECOS_API_KEY=
KRX_ID=
KRX_PW=
```

받은 값을 **등호 뒤에 그대로 붙여넣으면 끝**입니다. 따옴표·공백 없이:

```
FRED_API_KEY=abcdef0123456789abcdef0123456789
```

> `.env` 는 `.gitignore` 맨 위에 등록돼 있어서 GitHub에 절대 올라가지 않습니다.
> 키가 새는 사고의 대부분이 이걸 안 해서 생깁니다.

### 다 넣었으면 확인

```bash
cd c:/Research/stock-dashboard
.venv/Scripts/python.exe -m src.etl.check_keys
```

키마다 실제로 데이터를 한 건씩 받아보고 `[ OK ]` / `[ 실패 ]` 를 찍어줍니다.
아래 셋 중 하나를 받을 때마다 이 명령을 돌려보세요.

---

## 1. FRED — 미국 매크로 (급하지 않음)

**지금 없어도 대시보드는 돌아갑니다.** 키 없이 쓰는 공개 CSV 경로가 이미
동작 중이라, FRED 7종은 전부 수집되고 있습니다.

> 하이일드 OAS(`BAMLH0A0HYM2`)가 2023년 8월 이후만 오는 것은 **키와 무관**합니다.
> API 키로 받아도 785행으로 동일했고, 같은 호출로 `T10Y2Y` 는 1997년치까지
> 나옵니다. ICE BofA 라이선스 계열이라 FRED가 공개 범위를 3년으로 제한한
> 것으로 보입니다. 키를 넣는 이득은 히스토리가 아니라 **레이트리밋과
> 응답 안정성**입니다.

1. https://fredaccount.stlouisfed.org/login/secure/ 에서 계정을 만듭니다
   (이메일·비밀번호. 인증 메일의 링크를 눌러야 활성화됩니다)
2. 로그인한 상태로 https://fredaccount.stlouisfed.org/apikeys 로 갑니다
3. API 키를 새로 요청하는 버튼을 누릅니다. 용도를 묻는 칸이 있으면
   `personal research dashboard` 정도로 적으면 됩니다
4. **32자리 문자열이 화면에 바로 뜹니다.** 메일을 기다릴 필요 없습니다

```
FRED_API_KEY=여기에붙여넣기
```

---

## 2. ECOS — 한국은행 (한국 매크로를 원하면 필수)

한국 M2, 기준금리, **투자자예탁금**, 원/달러 환율이 여기서만 나옵니다.
키 없이 접근할 방법이 없습니다 (확인 결과 `INFO-100 인증키가 유효하지 않습니다`).

1. https://ecos.bok.or.kr/api/#/AuthKeyApply 로 갑니다
2. 이메일 주소와 사용 목적을 적고 신청합니다
3. **인증키가 입력한 이메일로 발송됩니다.** 승인 대기 없이 바로 옵니다
   (메일이 안 보이면 스팸함을 확인하세요)

```
ECOS_API_KEY=여기에붙여넣기
```

### ★ 키를 받은 뒤 한 단계가 더 있습니다

`config/series.yaml` 의 ecos 항목 4개가 아직 `TBD` 입니다:

```yaml
ecos:
  - key: m2
    stat_code: "TBD"     # ← 이걸 채워야 함
    item_code: "TBD"
```

한국은행은 통계마다 고유 코드가 있는데, 이건 **키가 있어야 목록을 조회**할 수
있습니다. 키를 넣고 아래를 실행하면 후보 코드들이 나옵니다:

```bash
.venv/Scripts/python.exe notebooks/00_explore_api.py --only ecos
```

키만 알려주시면 이 코드 확정까지 제가 처리하겠습니다.

---

## 3. KRX — 투자자별 수급 (본인인증 필요)

개인/외국인/기관 순매수 데이터입니다. 대시보드의 "한국 시장" 탭 하단이
지금 비어 있는 이유가 이것입니다.

**왜 필요해졌나**: pykrx 1.2 버전부터 `data.krx.co.kr` 로그인을 요구합니다.
공개 API를 직접 호출해도 `400 LOGOUT` 이 돌아오는 걸 확인했습니다.
CLAUDE.md 가 작성된 뒤에 바뀐 부분입니다.

1. http://data.krx.co.kr 접속 → 우측 상단 회원가입
2. 개인회원으로 가입 (휴대폰 본인인증이 필요합니다. 무료)
3. **별도의 키 발급이 아니라, 가입한 아이디와 비밀번호를 그대로 씁니다**

```
KRX_ID=가입한아이디
KRX_PW=가입한비밀번호
```

> 이건 진짜 계정 비밀번호라 다른 둘보다 조심해야 합니다. `.env` 는 커밋되지
> 않지만, 나중에 GitHub Actions 로 자동화할 때는 Repository Secrets 에
> 넣어야 합니다 (Phase 5).

### 없으면 뭘 못 하나

| 데이터 | 대안 | 상태 |
|---|---|---|
| KOSPI/KOSDAQ/KOSPI200 OHLCV | FinanceDataReader | ✅ 이미 수집 중 — 영향 없음 |
| 투자자별 순매수 | 없음 | ❌ KRX 계정 필요 |

---

## 4. 우선순위 정리

급한 순서대로:

| | 소요 | 없으면 |
|---|---|---|
| **ECOS** | 2분 | 한국 매크로 전부 없음 (예탁금·기준금리·환율) |
| **KRX** | 10분 (본인인증) | 수급 차트 없음 |
| **FRED** | 3분 | 하이일드 OAS 히스토리만 짧음 |

ECOS 하나만 받아도 대시보드가 눈에 띄게 채워집니다.

---

## 5. 텔레그램 알림 (선택)

RSI 과매수/과매도, 레짐 전환, 순유동성 급변, 수집 실패를 텔레그램으로 받습니다.
같은 상태가 이어지면 다시 울리지 않습니다 — **상태가 바뀔 때만** 전송합니다.

1. 텔레그램에서 **@BotFather** 검색 → 대화 시작
2. `/newbot` → 봇 이름과 사용자명 지정 (사용자명은 `bot` 으로 끝나야 함)
3. `123456789:AAF...` 형태의 **토큰**을 받는다
4. **방금 만든 봇을 검색해 대화방을 열고 아무 메시지나 하나 보낸다**
   (봇은 먼저 말을 걸 수 없다. 이걸 빠뜨리면 chat_id 를 못 찾는다)
5. 등록 — 토큰은 화면에 표시되지 않고 `.env` 에만 기록됩니다

```bash
.venv/Scripts/python.exe scripts/set_telegram.py    # 토큰 입력 → chat_id 자동 탐색
.venv/Scripts/python.exe -m src.alerts.run --test   # 연결 확인
```

토큰이 없어도 실행은 됩니다 — 전송 없이 출력만 하는 dry-run 으로 떨어집니다.
알림은 부가 기능이라 없다고 데이터 갱신이 멈추지 않습니다.

```bash
.venv/Scripts/python.exe -m src.alerts.run --dry-run   # 지금 무엇이 울릴지 확인
.venv/Scripts/python.exe -m src.alerts.run --reset     # 상태 초기화(전부 재전송)
```

---

## 6. GitHub Actions 자동화 시 (Phase 5)

`.env` 는 커밋되지 않으므로, 서버에서 돌리려면 같은 이름으로
Repository Secrets 에 따로 등록해야 합니다.
`FRED_API_KEY` / `ECOS_API_KEY` 는 등록 완료.

```bash
gh secret set KRX_ID             --repo yongsoo-choi0214/stock-dashboard
gh secret set KRX_PW             --repo yongsoo-choi0214/stock-dashboard
gh secret set TELEGRAM_BOT_TOKEN --repo yongsoo-choi0214/stock-dashboard
gh secret set TELEGRAM_CHAT_ID   --repo yongsoo-choi0214/stock-dashboard
```

값을 물으면 그때 입력하면 되고, 셸 히스토리에 남지 않습니다.

---

## 부록: KOFIA 일간 예탁금은 왜 못 붙였나

ECOS 예탁금(`901Y056/S23A`)은 **월간**이라 일간이 필요하면 금융투자협회
freesis 를 붙여야 한다. 여기까지는 확인했다.

- 투자자예탁금 메뉴 코드 `OS0021` → `parentDivId=MSIS10000000000000`,
  `serviceId=STATSCU0100000060` (`/resources/stat/js/menu.js`)
- 데이터 엔드포인트는 `POST /meta/getMetaDataList.do` (JSON only —
  form-urlencoded 은 415 를 돌려준다)

막힌 지점: freesis 는 eXbuilder6(cleopatra) SPA 이고, 이 엔드포인트는
서비스별로 다른 **DataMap 컬럼 이름**을 요구한다. 그 이름은 런타임에
동적으로 로드되는 서비스 모듈 안에 있어서 정적 JS 만 읽어서는 알 수 없다.
추측한 파라미터로 보낸 요청은 전부 서버 예외 페이지를 돌려받았다.

붙이려면 헤드리스 브라우저(playwright 등)로 실제 요청을 관찰해야 한다.
다만 그건 크론에 브라우저 의존성을 얹는 일이라, 월간 예탁금으로 충분한
현재로서는 권하지 않는다. CLAUDE.md 도 `kofia.py` 를 선택 항목으로 두었다.
