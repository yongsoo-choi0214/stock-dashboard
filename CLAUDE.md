# 매크로 유동성 + 기술적 지표 통합 주식 대시보드 — 프로젝트 명세

> **이 파일의 용도**: 레포 루트에 `CLAUDE.md`로 저장하세요. VS Code에서 Claude Code를 실행하면 이 파일을 자동으로 읽어 프로젝트 컨텍스트로 사용합니다. AI가 제멋대로 구조를 만들어내는 것을 막는 헌법 역할을 합니다.

---

## 1. 프로젝트 개요

한국 시장(KOSPI/KOSDAQ)을 주력으로, 한·미 매크로 유동성과 기술적 지표를 한 화면에서 추적하는 웹 대시보드.

| 항목 | 값 |
|---|---|
| 언어 | Python 3.11+ |
| 대시보드 | Streamlit + Plotly |
| 저장소 | Parquet 파일 (DB 서버 없음) |
| 자동화 | GitHub Actions (cron) |
| 배포 | Streamlit Community Cloud (public repo) |

### 설계 원칙 (위반 금지)

1. **ETL과 뷰를 분리한다.** Streamlit 앱은 외부 API를 **절대** 호출하지 않는다. `data/*.parquet`만 읽는다.
2. **지표 함수는 순수함수다.** 입력 Series를 받아 Series/DataFrame을 반환하며, I/O·전역상태·출력을 하지 않고 입력 index를 보존한다.
3. **시리즈 정의는 코드가 아니라 `config/series.yaml`에 둔다.** 코드에 시리즈 ID를 하드코딩하지 않는다.
4. **단위 정규화는 ETL 레이어에서 끝낸다.** 지표 레이어는 단위가 이미 맞다고 가정한다.
5. **한 소스의 실패가 전체를 죽이지 않는다.** 소스별 예외 격리.
6. **API 키는 절대 커밋하지 않는다.**

---

## 2. 폴더 구조

```
stock-dashboard/
├── CLAUDE.md                     # 이 문서 (AI 컨텍스트)
├── README.md
├── requirements.txt
├── .env                          # 로컬 키 (gitignore)
├── .env.example                  # 키 이름만
├── .gitignore
│
├── config/
│   ├── series.yaml               # 수집 대상 시리즈 선언
│   └── settings.py               # 경로/상수/env 로더
│
├── src/
│   ├── __init__.py
│   ├── etl/
│   │   ├── __init__.py
│   │   ├── base.py               # Fetcher 추상 인터페이스, 재시도, 증분로직
│   │   ├── fred.py               # 미국 매크로
│   │   ├── ecos.py               # 한국 매크로 + 예탁금
│   │   ├── krx.py                # 한국 지수/수급/펀더멘털
│   │   ├── us_equity.py          # yfinance
│   │   ├── kofia.py              # 예탁금 보조 (Phase 5, 선택)
│   │   └── run_all.py            # 엔트리포인트
│   │
│   ├── store.py                  # Parquet 읽기/쓰기/병합 (유일한 I/O 지점)
│   │
│   ├── indicators/
│   │   ├── __init__.py
│   │   ├── technical.py          # MACD, RSI, 이격도, 볼린저, z-score
│   │   └── liquidity.py          # 순유동성, 예탁금 파생
│   │
│   └── viz/
│       ├── __init__.py
│       ├── theme.py              # 색상/레이아웃 상수
│       └── charts.py             # Plotly figure 팩토리
│
├── app.py                        # Streamlit 엔트리포인트
├── pages/                        # Streamlit 멀티페이지 (선택)
│   ├── 1_Korea_Market.py
│   ├── 2_Liquidity.py
│   └── 3_Cross_Asset.py
│
├── data/                         # 산출물 (커밋 대상)
│   ├── macro.parquet
│   ├── prices.parquet
│   ├── flows.parquet
│   └── _meta.json                # 소스별 마지막 갱신 시각/상태
│
├── tests/
│   ├── test_technical.py         # ★ 필수
│   ├── test_liquidity.py
│   └── test_store.py
│
├── notebooks/
│   └── 00_explore_api.ipynb      # 원시 응답 확인 전용
│
└── .github/workflows/
    └── update_data.yml
```

### 왜 `data/`를 커밋하는가

일별 시계열 수십 년치는 Parquet으로 수 MB입니다. Git에 커밋하면 **저장소 + 버전관리 + 배포 채널**이 한 번에 해결되고, Streamlit Cloud는 레포를 클론하기만 하면 됩니다. DB 서버·클라우드 스토리지 비용이 0원입니다. 데이터가 수백 MB로 커지면 그때 DuckDB나 외부 스토리지로 옮기세요.

---

## 3. 데이터 스키마 (계약)

모든 ETL의 출력은 아래 두 스키마 중 하나로 강제됩니다. 이걸 지키면 하위 레이어가 소스를 몰라도 됩니다.

### 3.1 `macro.parquet` — 롱포맷 시계열

| 컬럼 | dtype | 설명 |
|---|---|---|
| `date` | `datetime64[ns]` | 관측일 (tz-naive, 자정 정규화) |
| `series_id` | `string` | 전역 고유 ID. `{source}.{code}` 형식 예: `fred.WALCL` |
| `value` | `float64` | 정규화 완료된 값 |

* 인덱스 없음(RangeIndex). `(date, series_id)`가 유니크 키.
* 롱포맷인 이유: 소스마다 주기·시작일이 제각각이라 와이드로 두면 NaN 밭이 되고 컬럼 추가 시 스키마가 깨집니다.

### 3.2 `prices.parquet` — OHLCV

| 컬럼 | dtype |
|---|---|
| `date` | `datetime64[ns]` |
| `ticker` | `string` (예: `KRX.1001`, `YF.^GSPC`) |
| `open`,`high`,`low`,`close` | `float64` |
| `volume` | `float64` |

### 3.3 `flows.parquet` — 투자자별 수급

| 컬럼 | dtype |
|---|---|
| `date` | `datetime64[ns]` |
| `market` | `string` (`KOSPI`/`KOSDAQ`) |
| `investor` | `string` (`개인`/`외국인`/`기관합계`/...) |
| `net_value` | `float64` (순매수 대금, 원) |

### 3.4 `_meta.json`

```json
{
  "fred":  {"last_run": "2026-08-05T18:00:12+09:00", "status": "ok",   "rows": 41230, "max_date": "2026-08-04"},
  "ecos":  {"last_run": "2026-08-05T18:00:31+09:00", "status": "ok",   "rows": 8812,  "max_date": "2026-06-30"},
  "krx":   {"last_run": "2026-08-05T18:01:05+09:00", "status": "fail", "error": "HTTPError 429"}
}
```

대시보드 상단에 이 정보를 띄우세요. **데이터가 언제 갱신됐는지 모르는 대시보드는 신뢰할 수 없습니다.**

---

## 4. `config/series.yaml`

```yaml
fred:
  - id: WALCL
    name: 연준 총자산
    unit_in: million_usd
    unit_out: billion_usd
    scale: 0.001
    freq: W
  - id: WTREGEN
    name: 재무부 일반계정(TGA)
    unit_in: billion_usd
    unit_out: billion_usd
    scale: 1.0
    freq: W
  - id: RRPONTSYD
    name: 익일물 역레포(ON RRP)
    unit_in: billion_usd
    unit_out: billion_usd
    scale: 1.0
    freq: D
  - id: M2SL
    name: 미국 M2
    unit_in: billion_usd
    unit_out: billion_usd
    scale: 1.0
    freq: M
    publication_lag_days: 30      # ★ 백테스트용 (§7.2)
  - id: DFF
    name: 연방기금 실효금리
    unit_in: percent
    unit_out: percent
    scale: 1.0
    freq: D
  - id: T10Y2Y
    name: 미국 10Y-2Y 스프레드
    unit_in: percent
    unit_out: percent
    scale: 1.0
    freq: D
  - id: BAMLH0A0HYM2
    name: 하이일드 OAS
    unit_in: percent
    unit_out: percent
    scale: 1.0
    freq: D

# ecos: 통계표코드/항목코드는 하드코딩하지 말고
#       StatisticTableList API로 탐색해 확정한 뒤 여기에 기록할 것 (§5.2)
ecos:
  - key: m2
    name: 한국 M2(평잔)
    stat_code: "TBD"
    item_code: "TBD"
    cycle: M
    publication_lag_days: 60
  - key: base_rate
    name: 한국은행 기준금리
    stat_code: "TBD"
    item_code: "TBD"
    cycle: D
  - key: investor_deposit
    name: 투자자예탁금
    stat_code: "TBD"
    item_code: "TBD"
    cycle: D
  - key: usdkrw
    name: 원/달러 환율
    stat_code: "TBD"
    item_code: "TBD"
    cycle: D

krx_index:
  - ticker: "1001"
    name: KOSPI
  - ticker: "2001"
    name: KOSDAQ
  - ticker: "1028"
    name: KOSPI200

yfinance:
  - ticker: "^GSPC"
    name: S&P500
  - ticker: "^IXIC"
    name: NASDAQ
  - ticker: "^VIX"
    name: VIX
  - ticker: "DX-Y.NYB"
    name: 달러인덱스
```

---

## 5. 파일별 책임 명세

AI에게 작업을 시킬 때 **아래 시그니처를 그대로 붙여넣으세요.** 인터페이스가 고정되면 모듈 간 결합이 안 깨집니다.

### 5.1 `src/store.py` — 유일한 I/O 지점

```python
from pathlib import Path
import pandas as pd

DATA_DIR: Path

def read(name: str) -> pd.DataFrame:
    """data/{name}.parquet 로드. 없으면 스키마만 맞는 빈 DataFrame."""

def write(name: str, df: pd.DataFrame) -> None:
    """스키마 검증 후 저장. dtype 강제."""

def upsert(name: str, new: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """기존 + 신규 병합. keys 기준 중복 제거(신규 우선), date 정렬 후 저장."""

def last_date(name: str, **filters) -> pd.Timestamp | None:
    """증분 수집용. 해당 조건의 마지막 관측일."""

def update_meta(source: str, status: str, **kw) -> None:
    """_meta.json 갱신."""
```

### 5.2 `src/etl/base.py`

```python
from abc import ABC, abstractmethod
import pandas as pd

class Fetcher(ABC):
    source: str          # "fred" | "ecos" | "krx" | "us_equity"
    target: str          # "macro" | "prices" | "flows"
    keys: list[str]      # upsert 키

    @abstractmethod
    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """원시 수집 → §3 스키마로 정규화하여 반환. 저장은 하지 않는다."""

    def run(self, lookback_days: int = 30) -> None:
        """last_date - lookback_days 부터 재수집 후 upsert.
        lookback을 두는 이유: 매크로 계열은 사후 수정(revision)이 잦다."""

def retry(times: int = 3, backoff: float = 2.0): ...
```

> `lookback_days`는 중요합니다. FRED·ECOS 데이터는 나중에 값이 **정정**됩니다. 마지막 날짜 이후만 받으면 과거 정정분을 영원히 놓칩니다.

### 5.3 `src/etl/fred.py`

```python
class FredFetcher(Fetcher):
    source, target, keys = "fred", "macro", ["date", "series_id"]

    def fetch(self, start, end) -> pd.DataFrame:
        """config/series.yaml의 fred 항목을 순회.
        - fredapi.Fred(api_key=settings.FRED_API_KEY).get_series(id, start, end)
        - value *= scale  (★ WALCL은 백만→십억)
        - series_id = f"fred.{id}"
        - 반환: [date, series_id, value]
        """
```

### 5.4 `src/etl/ecos.py`

```python
BASE = "https://ecos.bok.or.kr/api"

def list_tables(keyword: str) -> pd.DataFrame:
    """StatisticTableList 호출. 통계표코드 탐색용 — 노트북에서 1회 사용."""

def list_items(stat_code: str) -> pd.DataFrame:
    """StatisticItemList 호출. 항목코드 탐색용."""

class EcosFetcher(Fetcher):
    source, target, keys = "ecos", "macro", ["date", "series_id"]

    def fetch(self, start, end) -> pd.DataFrame:
        """StatisticSearch 호출.
        URL: {BASE}/StatisticSearch/{key}/json/kr/1/10000/{stat}/{cycle}/{s}/{e}/{item}
        - cycle별 날짜 포맷 상이: D=YYYYMMDD, M=YYYYMM, Q=YYYYQ1, A=YYYY
        - 응답 TIME 필드를 datetime으로 파싱 (M은 월말일로 정규화)
        - series_id = f"ecos.{key}"
        """
```

**ECOS 주의**: 응답이 `{"RESULT": {"CODE": "INFO-200"}}` 형태로 오면 데이터 없음이지 에러가 아닙니다. 정상 응답은 `{"StatisticSearch": {"list_total_count": N, "row": [...]}}`. 두 경우를 모두 처리하세요.

### 5.5 `src/etl/krx.py`

```python
class KrxIndexFetcher(Fetcher):
    source, target, keys = "krx", "prices", ["date", "ticker"]
    # pykrx.stock.get_index_ohlcv(start, end, ticker)
    # 컬럼명 한글("시가","고가","저가","종가","거래량") → 영문 매핑
    # ticker = f"KRX.{code}"

class KrxFlowFetcher(Fetcher):
    source, target, keys = "krx", "flows", ["date", "market", "investor"]
    # pykrx.stock.get_market_trading_value_by_investor(...)
```

**KRX 주의**: 웹 스크래핑이므로 호출 간 `time.sleep(0.3)` 필수. 장기 구간은 1년 단위로 쪼개 요청하세요.

### 5.6 `src/indicators/technical.py` — ★ 검증 완료 코드

아래는 실제 실행하여 정합성을 확인한 구현입니다. **그대로 쓰세요.**

```python
"""기술적 지표. 모든 함수는 순수함수이며 입력 Series의 index를 보존한다."""
from __future__ import annotations
import numpy as np
import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    """지수이동평균. alpha = 2/(span+1), 재귀식 정의(adjust=False)."""
    return s.ewm(span=span, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI. alpha = 1/n  (span=n 이 아님 — §7.1 참조)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0, 100.0)   # 연속 상승 구간
    out.iloc[:n] = np.nan                    # 워밍업
    return out.rename(f"rsi_{n}")


def disparity(close: pd.Series, n: int = 20) -> pd.Series:
    """이격도 = 종가 / SMA(n) * 100."""
    return (close / close.rolling(n).mean() * 100.0).rename(f"disparity_{n}")


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return pd.DataFrame({
        "mid": mid, "upper": mid + k * sd, "lower": mid - k * sd,
        "pct_b": (close - (mid - k * sd)) / (2 * k * sd),
    })


def zscore(s: pd.Series, window: int = 252) -> pd.Series:
    """롤링 z-score. 레짐 무관 비교를 위한 정규화."""
    mu = s.rolling(window).mean()
    sd = s.rolling(window).std(ddof=0)
    return ((s - mu) / sd.replace(0.0, np.nan)).rename(f"{s.name}_z")


def pct_rank(s: pd.Series, window: int = 252) -> pd.Series:
    """롤링 백분위 순위 [0,1]. 임계값 하드코딩 대신 사용 권장."""
    return s.rolling(window).rank(pct=True).rename(f"{s.name}_pr")
```

**검증 결과** (랜덤워크 600영업일 + 경계 케이스):

```
index 보존           : macd/rsi/disparity/bollinger 모두 True
RSI 범위             : [22.98, 76.04] ⊂ [0,100]  ✓
MACD 항등식          : max|hist - (macd-signal)| = 0.0  ✓
워밍업 NaN           : rsi14=14, disparity20=19  ✓
단조 상승 → RSI      : 100.0  ✓
단조 하락 → RSI      : 0.0    ✓
close==SMA → 이격도  : 100.0  ✓
pct_rank 범위        : ⊂ [0,1] ✓
```

### 5.7 `src/indicators/liquidity.py` — ★ 검증 완료 코드

```python
from __future__ import annotations
import pandas as pd


def us_net_liquidity(walcl: pd.Series, tga: pd.Series,
                     onrrp: pd.Series) -> pd.Series:
    """미국 순유동성 = 연준총자산 - TGA - ON RRP.

    입력 3개는 모두 **십억 USD로 사전 정규화**되어 있어야 한다.
    주기가 다르므로 수요일 기준 주간으로 정렬 후 계산한다.
    """
    df = pd.concat(
        {"walcl": walcl, "tga": tga, "onrrp": onrrp}, axis=1, sort=True
    ).sort_index()
    wk = df.resample("W-WED").last().ffill()
    out = (wk["walcl"] - wk["tga"] - wk["onrrp"]).rename("net_liquidity")
    # ★ 후행 stale 구간 절단 (§7.3)
    valid_until = min(walcl.index.max(), tga.index.max(), onrrp.index.max())
    return out[out.index <= valid_until]


def deposit_ratio(deposit: pd.Series, market_cap: pd.Series) -> pd.Series:
    """예탁금 / 시가총액 (%). 단위를 맞춘 뒤 호출할 것."""
    d, m = deposit.align(market_cap, join="inner")
    return (d / m * 100.0).rename("deposit_to_mcap")


def deposit_turnover(trading_value: pd.Series, deposit: pd.Series) -> pd.Series:
    """예탁금 회전율 = 일평균 거래대금 / 예탁금."""
    t, d = trading_value.align(deposit, join="inner")
    return (t / d).rename("deposit_turnover")


def to_change(s: pd.Series, periods: int = 1) -> pd.Series:
    """레벨보다 변화량이 신호력이 높은 계열용."""
    return s.diff(periods).rename(f"{s.name}_d{periods}")
```

**검증 결과**: 주간(WALCL/TGA) + 일간(ON RRP) 혼합 입력에서 W-WED 정렬 후 수동 계산값과 일치(6408.5 = 6408.5). 단위 변환(백만→십억) 반영 확인.

### 5.8 `src/viz/charts.py`

```python
import plotly.graph_objects as go
from plotly.subplots import make_subplots

def price_macd_rsi(df, title: str) -> go.Figure:
    """3단 서브플롯 (가격+MA / MACD+히스토그램 / RSI).
    - shared_xaxes=True, row_heights=[0.5, 0.25, 0.25]
    - RSI 패널에 70/30 수평선 (add_hline)
    - rangeslider 비활성 (좁은 화면에서 방해)
    """

def liquidity_overlay(liq, price, title: str) -> go.Figure:
    """유동성(좌축) + 지수(우축) 이중축. secondary_y=True"""

def disparity_bands(close, windows=(20, 60, 120)) -> go.Figure: ...
def investor_flow_bar(flows, market: str) -> go.Figure: ...
def kpi_row(metrics: dict) -> None:
    """st.columns + st.metric. delta로 전일/전주 대비 표시."""
```

### 5.9 `app.py`

```python
import streamlit as st

st.set_page_config(page_title="Market Dashboard", layout="wide")

@st.cache_data(ttl=3600)
def load(name: str):
    return store.read(name)

# 사이드바: 기간 선택, 지표 파라미터(RSI n, MACD fast/slow), 시장 선택
# 본문: _meta.json 기반 "최종 갱신: YYYY-MM-DD HH:MM" 배지 + KPI row + 탭
```

**`@st.cache_data` 없이 쓰면** 위젯을 건드릴 때마다 Parquet을 전부 다시 읽습니다. 반드시 붙이세요.

---

## 6. Phase별 로드맵과 완료 기준(DoD)

각 Phase의 DoD를 통과하기 전에는 다음으로 넘어가지 마세요.

### Phase 0 — 부트스트랩 (0.5일)

- [ ] FRED / ECOS API 키 발급
- [ ] venv + `requirements.txt`
- [ ] `.gitignore` **먼저** 작성 후 `.env` 생성
- [ ] GitHub public 레포 생성, 이 문서를 `CLAUDE.md`로 커밋

**DoD**: `git status`에 `.env`가 안 보인다.

### Phase 1 — 원시 응답 확인 (0.5일) ★ 건너뛰지 말 것

- [ ] `notebooks/00_explore_api.ipynb`에서 FRED / ECOS / pykrx 각각 **원시 응답을 print**
- [ ] ECOS는 `list_tables()`로 통계표코드 확정 → `series.yaml`의 `TBD` 채우기

**DoD**: 4개 소스 모두 실제 데이터가 눈으로 확인된다. 이 노트북 출력이 Phase 2에서 AI에게 줄 재료다.

### Phase 2 — ETL 레이어 (2일)

- [ ] `store.py` → `etl/base.py` → 소스별 fetcher 순서로 구현
- [ ] `run_all.py`가 소스별 예외를 격리하고 `_meta.json`을 갱신
- [ ] 증분 + lookback 재수집 동작

**DoD**: `python -m src.etl.run_all`을 **두 번 연속** 실행해도 행 수가 늘지 않는다(멱등성). `data/*.parquet`이 §3 스키마와 dtype까지 일치한다.

### Phase 3 — 지표 + 테스트 (1일)

- [ ] `indicators/` 구현 (§5.6·5.7 코드 사용)
- [ ] `tests/test_technical.py` — 경계 케이스 + 실제 KOSPI 값 대조

**DoD**: `pytest` 전체 통과. RSI(14)가 HTS/TradingView 값과 소수점 둘째 자리까지 일치.

### Phase 4 — 대시보드 (2일)

- [ ] `app.py` + 탭 4종 (Overview / Korea / Liquidity / Cross-Asset)
- [ ] 최종 갱신 배지, 사이드바 파라미터

**DoD**: `streamlit run app.py` 후 위젯 조작 시 1초 내 반응. 네트워크를 끊어도 정상 동작(= API 미호출 확인).

### Phase 5 — 자동화·배포 (0.5일)

```yaml
# .github/workflows/update_data.yml
name: update-data
on:
  schedule:
    - cron: "0 9 * * 1-5"      # UTC 09:00 = KST 18:00, 평일
  workflow_dispatch:
permissions:
  contents: write
jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11", cache: pip }
      - run: pip install -r requirements.txt
      - run: python -m src.etl.run_all
        env:
          FRED_API_KEY: ${{ secrets.FRED_API_KEY }}
          ECOS_API_KEY: ${{ secrets.ECOS_API_KEY }}
      - run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/
          git diff --staged --quiet || git commit -m "data: $(date -u +%Y-%m-%d)"
          git push
```

- [ ] Repository Secrets에 키 2개 등록
- [ ] `workflow_dispatch`로 수동 1회 실행 성공
- [ ] Streamlit Community Cloud 연결

**DoD**: 커밋을 안 해도 다음 영업일 18시에 `data/`가 자동 갱신되고 대시보드에 반영된다.

### Phase 6 — 연구 확장 (지속)

- [ ] `src/research/ic.py` — 지표별 Information Coefficient 시계열
- [ ] `src/research/regime.py` — 유동성 Δ × 모멘텀 2D 레짐
- [ ] 임계값 돌파 알림 (텔레그램)

---

## 7. 알려진 함정 (검증으로 확인된 것 포함)

### 7.1 RSI를 `ewm(span=n)`으로 구현 — 가장 흔한 오답

Wilder의 평활은 α = 1/n이고 `span=n`은 α = 2/(n+1)입니다. 랜덤워크 200틱에서 두 구현을 비교하면 **평균 절대 오차 5.6포인트, 최대 16.7포인트** 차이가 났습니다. 70/30 판정이 통째로 뒤집히는 크기입니다.

### 7.2 매크로 계열의 발표 시차 (look-ahead bias)

ECOS의 M2는 약 2개월 지연 공표됩니다. 백테스트에서 관측일(as-of) 기준으로 쓰면 **미래 정보가 새어 들어갑니다.** 학습 데이터에 test set이 섞이는 것과 정확히 같은 오류입니다. `series.yaml`의 `publication_lag_days`만큼 shift한 뒤 사용하세요.

### 7.3 주기 혼합 시 후행 stale 구간 — 실제로 발견된 버그

주간(WALCL) + 일간(ON RRP)을 `resample().ffill()`하면, 일간 계열이 더 최근까지 존재할 때 **주간 계열의 마지막 값이 앞으로 복사되어** 존재하지 않는 관측치가 생깁니다. 검증에서 실제로 60행 입력에 61행이 나왔고, 마지막 행은 walcl/tga가 stale이었습니다. §5.7의 `valid_until` 절단이 이 대응입니다. 대시보드 우측 끝 몇 포인트가 미묘하게 틀리는 형태로 나타나서 눈치채기 어렵습니다.

### 7.4 FRED 단위 불일치

`WALCL`만 백만 USD, `WTREGEN`·`RRPONTSYD`는 십억 USD. 정규화 없이 빼면 순유동성이 완전히 엉뚱하게 나옵니다. `series.yaml`의 `scale`로 처리하세요.

### 7.5 기타

| 함정 | 대응 |
|---|---|
| Streamlit이 렌더링마다 API 호출 | ETL/뷰 분리 + `@st.cache_data` |
| pykrx 과다 요청 차단 | `time.sleep(0.3)`, 1년 단위 분할 |
| 휴장일 정렬 오류 | 거래일 캘린더 기준 index 통일 |
| 매크로 값의 사후 정정 누락 | `lookback_days` 재수집 |
| ECOS `INFO-200` 응답을 에러로 처리 | 데이터 없음으로 분기 |
| API 키 커밋 | `.gitignore` 먼저, Secrets 사용 |

---

## 8. Claude Code 사용 규칙

### 세션당 한 Phase만

"전체를 만들어줘"는 금물입니다. Phase 단위로 끊고, 각 Phase의 DoD를 프롬프트에 명시하세요.

### 항상 실행과 출력 확인을 요구

```
작성 후 실제로 실행해서 결과 DataFrame의 head/tail/dtypes를 보여줘.
```

이 한 줄이 환각 코드의 대부분을 걸러냅니다.

### 데이터 소스 연동은 실제 응답 샘플을 붙여넣고 시킬 것

스펙 문서만 주면 존재하지 않는 엔드포인트·파라미터를 그럴듯하게 지어냅니다. Phase 1 노트북 출력이 이때 쓰입니다.

### 지표 코드는 리뷰 대상

§5.6·5.7은 검증된 코드입니다. AI가 "더 간결하게" 리팩터링하겠다고 하면 거절하거나, 반드시 `pytest`로 확인하세요.

### 첫 세션 프롬프트 예시

```
CLAUDE.md를 읽었지? 오늘은 Phase 2(ETL 레이어)만 진행한다.

1. src/store.py 부터. §5.1 시그니처 그대로 구현.
   스키마는 §3.1/3.2/3.3, dtype까지 강제할 것.
2. src/etl/base.py — §5.2 인터페이스.
3. src/etl/fred.py — §5.3. WALCL scale=0.001 반드시 적용.
4. 구현 후 실제 실행해서 data/macro.parquet의
   head/tail/dtypes/series_id 목록을 보여줘.
5. 이어서 run_all.py를 두 번 실행해 행 수가 동일한지(멱등성) 확인.

ecos.py, krx.py는 아직 건드리지 마. fred 하나가 완전히 동작한 뒤에 간다.
```

---

## 9. `requirements.txt`

```
pandas>=2.2
numpy>=1.26
pyarrow>=15.0
pyyaml>=6.0
python-dotenv>=1.0
requests>=2.31
fredapi>=0.5.2
pykrx>=1.0.45
finance-datareader>=0.9.90
yfinance>=0.2.40
streamlit>=1.38
plotly>=5.22
pytest>=8.0
```

버전은 하한만 두되, 배포 후 문제가 생기면 `pip freeze > requirements.lock.txt`로 고정하세요. Streamlit Cloud는 requirements를 그대로 설치하므로 여기 없는 패키지는 배포 시 ImportError가 납니다.

---

## 10. 즉시 실행 체크리스트

```bash
mkdir stock-dashboard && cd stock-dashboard
python -m venv .venv && source .venv/bin/activate
printf '.env\n.venv/\n__pycache__/\n.ipynb_checkpoints/\n' > .gitignore
printf 'FRED_API_KEY=\nECOS_API_KEY=\n' > .env
printf 'FRED_API_KEY=\nECOS_API_KEY=\n' > .env.example
# CLAUDE.md 저장 (이 문서)
git init && git add -A && git commit -m "chore: bootstrap"
gh repo create stock-dashboard --public --source=. --push
code .
claude          # Phase 1 프롬프트 투입
```
