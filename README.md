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

레포: https://github.com/yongsoo-choi0214/stock-dashboard

| Phase | 내용 | 상태 |
|---|---|---|
| 0 | 부트스트랩 (구조/venv/gitignore) | ✅ |
| 1 | 원시 응답 확인 | ✅ (KRX 수급만 계정 대기) |
| 2 | ETL 레이어 (store → base → fetcher) | ✅ 4종 (krx_flow 대기) |
| 3 | 지표 + 테스트 | ✅ |
| 4 | 대시보드 | ✅ 탭 5종 |
| 5 | 자동화 | ✅ Actions 자율 커밋 확인 / ⏸ Streamlit Cloud 연결 |
| 6 | 연구 확장 | ✅ IC · 레짐 |

테스트 88개 통과.

### 현재 수집되는 데이터

| 파일 | 행 | 내용 |
|---|---|---|
| `macro.parquet` | 60,345 | FRED 7종 + ECOS 9종 |
| `prices.parquet` | 37,736 | KOSPI/KOSDAQ/KOSPI200 + S&P500/나스닥/VIX/달러인덱스 |
| `flows.parquet` | 0 | 개인·기관 수급 — `KRX_ID`/`KRX_PW` 필요 |

ECOS 9종: M2(평잔) · 기준금리 · 원달러 · 투자자예탁금 · KOSPI 시가총액 ·
거래대금(KOSPI/KOSDAQ) · 외국인 순매수(KOSPI/KOSDAQ).
`802Y001` 덕분에 KRX 계정 없이도 외국인 수급·시총·거래대금이 확보된다.

### 배포 남은 절차 (웹 UI 필요)

**새 계정을 만들지 않는다. GitHub 계정으로 로그인한다.**

1. https://share.streamlit.io → **Continue with GitHub** → GitHub 로그인
2. **Authorize streamlit** (public 레포 읽기 권한)
3. **Create app** → 이미 있는 앱을 배포하는 쪽 선택
   (템플릿에서 새로 만드는 경로가 아니다)
4. 입력값
   - Repository: `yongsoo-choi0214/stock-dashboard`
   - Branch: `main`
   - Main file path: `app.py`
5. **Advanced settings** 에서 Python 3.11 선택 — 로컬 검증 환경과 맞춘다
6. Deploy

Secrets 는 **넣지 않는다.** 앱은 커밋된 parquet 만 읽으므로 API 키가 필요 없다
(설계원칙 1). 키는 GitHub Actions 쪽 Repository Secrets 에만 있으면 된다.

배포 후에는 Actions 가 `data/` 를 커밋할 때마다 Streamlit Cloud 가 자동으로
다시 배포한다.

### 명세와 달라진 점

- **`WTREGEN`(TGA)은 백만 USD 단위**다. CLAUDE.md §7.4는 십억이라 적었으나
  원시 최대값 1,816,687(=2020년 TGA $1.82조)이 백만임을 확정한다.
  `series.yaml` 의 `scale: 0.001` + `expect_range` 로 처리·감시한다.
- **pykrx 1.2+ 는 data.krx.co.kr 로그인이 필수**다. 지수 OHLCV 는
  FinanceDataReader 로 대체했고(`ticker` 계약은 `KRX.{code}` 유지),
  투자자 수급만 계정에 의존한다. → [docs/SETUP_KEYS.md](docs/SETUP_KEYS.md)
- **FRED 는 키 없이도 동작**한다(`fredgraph.csv`). 키가 있으면 `fredapi` 로
  자동 전환된다. 두 경로의 반환 행 수는 동일하므로 키의 이득은 히스토리가
  아니라 레이트리밋·안정성이다.
- **하이일드 OAS 는 3년치만 공개된다.** ICE BofA 라이선스 계열이라 키가 있어도
  같다(785행). 같은 API로 `T10Y2Y` 는 1997년치가 나오므로 인증 문제가 아니다.
- **투자자예탁금은 ECOS `901Y056`(증시주변자금동향)에 있고 월간만 제공**된다.
  일간이 필요하면 KOFIA(freesis)를 붙여야 한다.
