"""자격증명 점검기.

.env 에 넣은 키가 실제로 동작하는지 하나씩 확인한다.
키를 새로 받을 때마다 이걸 돌려보면 된다.

    python -m src.etl.check_keys
"""
from __future__ import annotations

import os
import sys

import requests

from config import settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OK, NG, SKIP = "[ OK ]", "[ 실패 ]", "[ 없음 ]"


def check_fred() -> bool:
    key = settings.FRED_API_KEY
    if not key:
        print(f"{SKIP} FRED  — 키 없음. 무키 CSV 경로로 동일한 데이터를 받고 있습니다.")
        print("        (검증 결과 두 경로의 행 수가 같아, 키는 안정성 목적입니다)")
        return True   # 없어도 대시보드는 동작하므로 실패로 치지 않는다
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": "WALCL", "api_key": key, "file_type": "json",
                    "limit": 1, "sort_order": "desc"},
            timeout=30)
    except Exception as e:
        print(f"{NG} FRED  — 접속 실패: {type(e).__name__}: {e}")
        return False

    if r.status_code == 200:
        obs = r.json().get("observations", [{}])[0]
        print(f"{OK} FRED  — 최신 관측 {obs.get('date')} = {obs.get('value')}")
        return True
    if r.status_code == 400 and "api_key" in r.text.lower():
        print(f"{NG} FRED  — 키가 올바르지 않습니다. 32자리 소문자+숫자인지 확인하세요.")
        print(f"        (현재 값: {len(key)}자)")
        return False
    print(f"{NG} FRED  — HTTP {r.status_code}: {r.text[:150]}")
    return False


def check_ecos() -> bool:
    key = settings.ECOS_API_KEY
    if not key:
        print(f"{SKIP} ECOS  — 키 없음. 한국 매크로(M2·기준금리·예탁금·환율)를 못 받습니다.")
        return False
    try:
        r = requests.get(
            f"https://ecos.bok.or.kr/api/StatisticTableList/{key}/json/kr/1/5/",
            timeout=30)
        js = r.json()
    except Exception as e:
        print(f"{NG} ECOS  — 접속 실패: {type(e).__name__}: {e}")
        return False

    if "RESULT" in js:
        code = js["RESULT"].get("CODE")
        msg = js["RESULT"].get("MESSAGE", "")
        if code == "INFO-100":
            print(f"{NG} ECOS  — 인증키가 유효하지 않습니다. 메일로 받은 키를 다시 확인하세요.")
        else:
            print(f"{NG} ECOS  — {code}: {msg}")
        return False

    n = js.get("StatisticTableList", {}).get("list_total_count", "?")
    print(f"{OK} ECOS  — 통계표 {n}건 조회 가능")
    return True


def check_krx() -> bool:
    uid, pw = os.getenv("KRX_ID"), os.getenv("KRX_PW")
    if not (uid and pw):
        print(f"{SKIP} KRX   — 계정 없음. 투자자별 수급(flows)을 못 받습니다.")
        print("        지수 OHLCV 는 FinanceDataReader 로 이미 받고 있어 영향 없습니다.")
        return False
    try:
        from pykrx import stock
        df = stock.get_market_trading_value_by_investor(
            "20240102", "20240105", "KOSPI")
    except Exception as e:
        print(f"{NG} KRX   — 로그인/조회 실패: {type(e).__name__}: {e}")
        print("        아이디/비밀번호가 data.krx.co.kr 계정인지 확인하세요.")
        return False

    if df is None or df.empty:
        print(f"{NG} KRX   — 로그인은 됐으나 빈 응답입니다.")
        return False
    print(f"{OK} KRX   — 투자자 {len(df)}개 구분 수신 ({', '.join(map(str, df.index[:3]))} …)")
    return True


def main() -> int:
    print(f"\n.env 위치: {settings.ROOT / '.env'}\n")
    results = {"fred": check_fred(), "ecos": check_ecos(), "krx": check_krx()}

    print("\n다음 할 일")
    if not results["ecos"]:
        print("  - ECOS 키를 받으면: config/series.yaml 의 ecos TBD 4개를 채워야 합니다.")
        print("    (탐색: python notebooks/00_explore_api.py --only ecos)")
    if not results["krx"]:
        print("  - KRX 계정을 만들면: python -m src.etl.run_all --only krx_flow")
    if all(results.values()):
        print("  - 전부 통과. python -m src.etl.run_all 로 전체 수집하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
