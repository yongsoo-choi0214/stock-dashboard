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
| 1 | 원시 응답 확인 (FRED/ECOS/pykrx/yfinance) | 진행 중 |
| 2 | ETL 레이어 (store → base → fetcher) | |
| 3 | 지표 + 테스트 | |
| 4 | 대시보드 | |
| 5 | 자동화·배포 | |
| 6 | 연구 확장 | |
