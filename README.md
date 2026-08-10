# 매크로 유동성 + 기술적 지표 통합 주식 대시보드

한국 시장(KOSPI/KOSDAQ)을 주력으로, 한·미 매크로 유동성과 기술적 지표를 한 화면에서 추적하는 웹 대시보드.

> 설계 명세·규칙은 [CLAUDE.md](CLAUDE.md)가 단일 출처(single source of truth)입니다.

## 구조

```
config/     시리즈 선언(series.yaml) + 경로/키 로더(settings.py)
src/etl/    외부 API 수집 → 스키마 정규화 (여기서만 네트워크 호출)
src/store.py Parquet 읽기/쓰기/병합 (유일한 I/O 지점)
src/indicators/ 순수함수 지표 (technical / liquidity)
src/viz/    Plotly figure 팩토리
app.py      Streamlit 엔트리포인트 (data/*.parquet만 읽음, API 호출 금지)
data/       Parquet 산출물 (커밋 대상)
```

## 셋업

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows
pip install -r requirements.txt
cp .env.example .env            # 키 채우기
```

발급처
- FRED: https://fredaccount.stlouisfed.org/apikeys
- ECOS: https://ecos.bok.or.kr/api/#/AuthKeyApply

## 실행

```bash
python -m src.etl.run_all       # 데이터 갱신 (증분 + lookback 재수집)
pytest                          # 지표 검증
streamlit run app.py            # 대시보드
```

## 진행 상황

| Phase | 내용 | 상태 |
|---|---|---|
| 0 | 부트스트랩 (구조/venv/gitignore) | ✅ |
| 1 | 원시 응답 확인 | ✅ FRED·FDR·yfinance / ⏸ ECOS·KRX수급 (키 대기) |
| 2 | ETL 레이어 (store → base → fetcher) | ✅ 무키 3종 (ecos·krx_flow 대기) |
| 3 | 지표 + 테스트 | ✅ 53개 통과 |
| 4 | 대시보드 | ✅ |
| 5 | 자동화·배포 | |
| 6 | 연구 확장 | |

### 현재 수집되는 데이터

| 파일 | 행 | 내용 |
|---|---|---|
| `macro.parquet` | 19,879 | FRED 7종 (연준총자산·TGA·역레포·M2·FF금리·10Y-2Y·HY OAS) |
| `prices.parquet` | 37,733 | KOSPI/KOSDAQ/KOSPI200 + S&P500/나스닥/VIX/달러인덱스 |
| `flows.parquet` | 0 | 투자자 수급 — `KRX_ID`/`KRX_PW` 필요 |

### 명세와 달라진 점

- **`WTREGEN`(TGA)은 백만 USD 단위**다. CLAUDE.md §7.4는 십억이라 적었으나
  원시 최대값 1,816,687(=2020년 TGA $1.82조)이 백만임을 확정한다.
  `series.yaml` 의 `scale: 0.001` + `expect_range` 로 처리·감시한다.
- **pykrx 1.2+ 는 data.krx.co.kr 로그인이 필수**다. 지수 OHLCV 는
  FinanceDataReader 로 대체했고(`ticker` 계약은 `KRX.{code}` 유지),
  투자자 수급만 계정에 의존한다. → [docs/SETUP_KEYS.md](docs/SETUP_KEYS.md)
- **FRED 는 키 없이도 동작**한다(`fredgraph.csv`). 키를 넣으면 `fredapi` 로
  자동 전환되며, 그때 하이일드 OAS 의 전체 히스토리가 복구된다.
