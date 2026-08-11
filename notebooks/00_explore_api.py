"""Phase 1 — 원시 응답 확인 전용 스크립트.

CLAUDE.md는 노트북(.ipynb)을 지정하지만, CLI에서 출력을 그대로 확인·기록하기 위해
동일 내용을 실행 가능한 스크립트로 둔다. 셀 단위 실행은 `--only` 로 대신한다.

사용법:
    python notebooks/00_explore_api.py                # 전체
    python notebooks/00_explore_api.py --only krx     # 한 소스만
    python notebooks/00_explore_api.py --only ecos --keyword 예탁금

여기서 확인한 원시 응답이 Phase 2 ETL 구현의 재료다. 절대 이 파일에서 저장하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows 콘솔 기본 코덱이 cp949라 한글/em-dash 출력 시 UnicodeEncodeError가 난다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from config import settings  # noqa: E402


def hr(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


# ---------------------------------------------------------------- FRED
def explore_fred() -> None:
    hr("FRED — fredapi.get_series 원시 응답")
    if not settings.FRED_API_KEY:
        print("!! FRED_API_KEY 없음 — .env 를 채우세요")
        return
    from fredapi import Fred

    fred = Fred(api_key=settings.FRED_API_KEY)
    for item in settings.series_for("fred"):
        sid = item["id"]
        try:
            s = fred.get_series(sid, observation_start="2024-01-01")
        except Exception as e:  # 소스별 예외 격리 (설계원칙 5)
            print(f"[{sid}] FAIL {type(e).__name__}: {e}")
            continue
        print(f"\n[{sid}] {item['name']}  freq={item['freq']}  scale={item['scale']}")
        print(f"  type={type(s).__name__} len={len(s)} index={type(s.index).__name__}")
        print(f"  head:\n{s.head(3).to_string()}")
        print(f"  tail:\n{s.tail(3).to_string()}")
        print(f"  scale 적용 후 마지막 값: {s.iloc[-1] * item['scale']:.3f} {item['unit_out']}")


# ---------------------------------------------------------------- ECOS
ECOS_BASE = "https://ecos.bok.or.kr/api"


def ecos_get(path: str) -> dict:
    """원시 JSON 그대로 반환. INFO-200(데이터 없음)도 예외가 아니다 (§5.4)."""
    import requests

    url = f"{ECOS_BASE}/{path}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def explore_ecos(keyword: str | None = None) -> None:
    hr("ECOS — StatisticTableList / StatisticItemList / StatisticSearch")
    key = settings.ECOS_API_KEY
    if not key:
        print("!! ECOS_API_KEY 없음 — .env 를 채우세요")
        return

    # 1) 통계표 목록
    js = ecos_get(f"StatisticTableList/{key}/json/kr/1/1000/")
    if "RESULT" in js:
        print("RESULT 응답:", js["RESULT"])
        return
    rows = js["StatisticTableList"]["row"]
    print(f"통계표 총 {js['StatisticTableList']['list_total_count']}건, 수신 {len(rows)}건")
    print("샘플 원시 row:", json.dumps(rows[0], ensure_ascii=False))

    kws = [keyword] if keyword else ["통화", "기준금리", "예탁금", "환율"]
    for kw in kws:
        hits = [r for r in rows if kw in (r.get("STAT_NAME") or "")]
        print(f"\n--- '{kw}' 포함 통계표 {len(hits)}건 (상위 10)")
        for r in hits[:12]:
            # CYCLE 은 분류(대주제) 행에서 null 로 온다 — 실제 통계표만 주기가 있다
            cyc = r.get("CYCLE") or "-"
            print(f"  {r['STAT_CODE']:<12} cycle={cyc:<4} {r['STAT_NAME']}")


def explore_ecos_items(stat_code: str) -> None:
    hr(f"ECOS — StatisticItemList / {stat_code}")
    key = settings.ECOS_API_KEY
    js = ecos_get(f"StatisticItemList/{key}/json/kr/1/1000/{stat_code}/")
    if "RESULT" in js:
        print("RESULT 응답:", js["RESULT"])
        return
    rows = js["StatisticItemList"]["row"]
    print(f"항목 {len(rows)}건")
    print("샘플 원시 row:", json.dumps(rows[0], ensure_ascii=False))
    for r in rows[:40]:
        print(f"  {r.get('ITEM_CODE'):<16} {r.get('CYCLE','?'):<4} "
              f"{r.get('START_TIME')}~{r.get('END_TIME')}  {r.get('ITEM_NAME')}")


def explore_ecos_search(stat_code: str, cycle: str, start: str, end: str,
                        item_code: str = "") -> None:
    hr(f"ECOS — StatisticSearch / {stat_code} / {cycle} / {item_code or '(item 없음)'}")
    key = settings.ECOS_API_KEY
    path = f"StatisticSearch/{key}/json/kr/1/100/{stat_code}/{cycle}/{start}/{end}/{item_code}"
    js = ecos_get(path)
    print("원시 응답 키:", list(js.keys()))
    if "RESULT" in js:
        print("RESULT:", js["RESULT"], "→ 데이터 없음(에러 아님)")
        return
    body = js["StatisticSearch"]
    print(f"list_total_count={body['list_total_count']}")
    for r in body["row"][:3]:
        print("  ", json.dumps(r, ensure_ascii=False))
    print("  ...")
    for r in body["row"][-3:]:
        print("  ", json.dumps(r, ensure_ascii=False))


# ---------------------------------------------------------------- pykrx
def explore_krx() -> None:
    hr("pykrx — 지수 OHLCV / 투자자별 거래대금")
    from pykrx import stock

    for item in settings.series_for("krx_index"):
        code = item["ticker"]
        df = stock.get_index_ohlcv("20240102", "20240110", code)
        print(f"\n[KRX.{code}] {item['name']}")
        print(f"  shape={df.shape} index={type(df.index).__name__} name={df.index.name}")
        print(f"  columns={list(df.columns)}")
        print(df.head(3).to_string())
        print(f"  dtypes:\n{df.dtypes.to_string()}")

    df = stock.get_market_trading_value_by_investor("20240102", "20240110", "KOSPI")
    print("\n[flows] get_market_trading_value_by_investor('KOSPI')")
    print(f"  shape={df.shape} index.name={df.index.name} columns={list(df.columns)}")
    print(df.to_string())
    print(f"  dtypes:\n{df.dtypes.to_string()}")


# ---------------------------------------------------------- FinanceDataReader
def explore_fdr() -> None:
    """pykrx 가 KRX 로그인을 요구하므로, 한국 지수 OHLCV의 무인증 경로를 확인한다."""
    hr("FinanceDataReader — 한국 지수 OHLCV (로그인 불필요)")
    import FinanceDataReader as fdr

    for code, name in [("KS11", "KOSPI"), ("KQ11", "KOSDAQ"), ("KS200", "KOSPI200")]:
        try:
            df = fdr.DataReader(code, "2024-01-02", "2024-01-10")
        except Exception as e:
            print(f"[{code}] FAIL {type(e).__name__}: {e}")
            continue
        print(f"\n[{code}] {name} shape={df.shape}")
        print(f"  columns={list(df.columns)}")
        print(df.head(3).to_string())
        print(f"  dtypes:\n{df.dtypes.to_string()}")


# ---------------------------------------------------------------- yfinance
def explore_yf() -> None:
    hr("yfinance — download 원시 응답")
    import yfinance as yf

    tickers = [i["ticker"] for i in settings.series_for("yfinance")]
    df = yf.download(tickers, start="2024-01-02", end="2024-01-10",
                     auto_adjust=False, progress=False, group_by="ticker")
    print(f"shape={df.shape}")
    print(f"columns(MultiIndex={isinstance(df.columns, __import__('pandas').MultiIndex)}):")
    print(f"  {list(df.columns)[:8]} ...")
    print(df.head(3).to_string())
    print(f"index dtype={df.index.dtype}, tz={getattr(df.index, 'tz', None)}")


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only",
                    choices=["fred", "ecos", "krx", "fdr", "yf", "items", "search"])
    ap.add_argument("--keyword")
    ap.add_argument("--stat")
    ap.add_argument("--item", default="")
    ap.add_argument("--cycle", default="D")
    ap.add_argument("--start", default="20240101")
    ap.add_argument("--end", default="20240131")
    a = ap.parse_args()

    if a.only == "items":
        explore_ecos_items(a.stat)
    elif a.only == "search":
        explore_ecos_search(a.stat, a.cycle, a.start, a.end, a.item)
    elif a.only:
        {"fred": explore_fred, "krx": explore_krx, "fdr": explore_fdr,
         "yf": explore_yf, "ecos": lambda: explore_ecos(a.keyword)}[a.only]()
    else:
        for fn in (explore_fred, lambda: explore_ecos(a.keyword),
                   explore_krx, explore_fdr, explore_yf):
            try:
                fn()
            except Exception as e:
                print(f"\n!! {type(e).__name__}: {e}")
