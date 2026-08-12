"""한국 매크로 — 한국은행 ECOS (CLAUDE.md §5.4).

주의점 두 가지가 이 파일의 대부분이다.
- 주기마다 날짜 포맷이 다르다: D=YYYYMMDD, M=YYYYMM, Q=YYYYQn, A=YYYY
- 응답이 {"RESULT": {"CODE": "INFO-200"}} 이면 **데이터 없음이지 에러가 아니다**.
  INFO-100(인증키 오류) 등 진짜 에러와 구분해야 한다.
"""
from __future__ import annotations

import time

import pandas as pd
import requests

from config import settings
from src.etl.base import Fetcher, retry

BASE = "https://ecos.bok.or.kr/api"
PAGE = 10000          # 1회 요청 최대 행 수
SLEEP = 0.15          # 호출 간 간격

#: 데이터 없음(에러 아님)
NO_DATA = {"INFO-200"}


class EcosError(RuntimeError):
    pass


# ------------------------------------------------------------------ 탐색용
def _get(path: str) -> dict:
    r = requests.get(f"{BASE}/{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def list_tables(keyword: str = "") -> pd.DataFrame:
    """StatisticTableList 호출. 통계표코드 탐색용 — 노트북에서 1회 사용."""
    js = _get(f"StatisticTableList/{settings.ECOS_API_KEY}/json/kr/1/1000/")
    if "RESULT" in js:
        raise EcosError(js["RESULT"])
    df = pd.DataFrame(js["StatisticTableList"]["row"])
    if keyword:
        df = df[df["STAT_NAME"].fillna("").str.contains(keyword)]
    return df


def list_items(stat_code: str) -> pd.DataFrame:
    """StatisticItemList 호출. 항목코드 탐색용."""
    js = _get(f"StatisticItemList/{settings.ECOS_API_KEY}/json/kr/1/1000/{stat_code}/")
    if "RESULT" in js:
        raise EcosError(js["RESULT"])
    return pd.DataFrame(js["StatisticItemList"]["row"])


# ------------------------------------------------------------------ 날짜
def fmt_time(ts: pd.Timestamp, cycle: str) -> str:
    """주기별 요청 날짜 문자열."""
    if cycle == "D":
        return ts.strftime("%Y%m%d")
    if cycle == "M":
        return ts.strftime("%Y%m")
    if cycle == "Q":
        return f"{ts.year}Q{ts.quarter}"
    if cycle == "A":
        return str(ts.year)
    raise ValueError(f"지원하지 않는 주기: {cycle}")


def parse_time(s: str, cycle: str) -> pd.Timestamp:
    """응답 TIME → datetime. 기간형(M/Q/A)은 **기간 말일**로 정규화한다.

    말일로 두는 이유: 월 데이터를 1일로 찍으면 일간 계열과 resample 할 때
    그 달의 값이 달 시작에 붙어 실제보다 한 달 앞선 것처럼 보인다.
    """
    s = str(s).strip()
    if cycle == "D":
        return pd.Timestamp(pd.to_datetime(s, format="%Y%m%d"))
    if cycle == "M":
        return pd.Timestamp(pd.to_datetime(s, format="%Y%m")) + pd.offsets.MonthEnd(0)
    if cycle == "Q":
        year, q = int(s[:4]), int(s[-1])
        return pd.Timestamp(year=year, month=q * 3, day=1) + pd.offsets.MonthEnd(0)
    if cycle == "A":
        return pd.Timestamp(year=int(s), month=12, day=31)
    raise ValueError(f"지원하지 않는 주기: {cycle}")


# ------------------------------------------------------------------ 수집
@retry(times=3)
def _search_page(stat: str, cycle: str, start: str, end: str, item: str,
                 offset: int, item2: str = "") -> tuple[list[dict], int]:
    path = (f"StatisticSearch/{settings.ECOS_API_KEY}/json/kr/"
            f"{offset + 1}/{offset + PAGE}/{stat}/{cycle}/{start}/{end}/{item}")
    if item2:
        # 2차 항목(Group2)이 있는 통계표는 이걸 줘야 TIME 이 유일해진다.
        # 예: 기업경기조사 = 업종(Group1) × BSI 종류(Group2)
        path += f"/{item2}"
    js = _get(path)

    if "RESULT" in js:
        code = js["RESULT"].get("CODE", "")
        if code in NO_DATA:
            return [], 0                     # 데이터 없음 — 정상 분기
        raise EcosError(f"{code}: {js['RESULT'].get('MESSAGE', '')}")

    body = js["StatisticSearch"]
    return body.get("row", []), int(body.get("list_total_count", 0))


def fetch_series(stat: str, cycle: str, item: str,
                 start: pd.Timestamp, end: pd.Timestamp,
                 item2: str = "") -> pd.Series:
    """한 시리즈 전체를 페이지네이션으로 받아 Series 로 반환."""
    s_str, e_str = fmt_time(start, cycle), fmt_time(end, cycle)
    rows: list[dict] = []
    offset = 0
    while True:
        page, total = _search_page(stat, cycle, s_str, e_str, item, offset, item2)
        rows.extend(page)
        offset += PAGE
        time.sleep(SLEEP)
        if not page or offset >= total:
            break

    if not rows:
        return pd.Series(dtype="float64")

    df = pd.DataFrame(rows)
    # Group2(구분코드)가 있는 통계표는 같은 TIME 이 여러 번 나온다.
    # 조용히 하나만 고르면 틀린 계열을 잡을 수 있으므로 명시적으로 막는다.
    if df["TIME"].duplicated().any():
        extra = sorted(set(df.get("ITEM_NAME2", pd.Series(dtype=object)).dropna()))
        raise EcosError(
            f"{stat}/{item}: TIME 중복. 2차 항목(Group2)을 지정해야 합니다. "
            f"후보={extra[:5]}")

    idx = df["TIME"].map(lambda t: parse_time(t, cycle))
    val = pd.to_numeric(df["DATA_VALUE"], errors="coerce")
    return pd.Series(val.to_numpy(), index=pd.DatetimeIndex(idx)).dropna().sort_index()


class EcosFetcher(Fetcher):
    source, target, keys = "ecos", "macro", ["date", "series_id"]
    # 시리즈 9종 × 페이지 몇 개. 전체 재수집이 싸고 정정 반영에 확실하다.
    full_refresh = True

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        if not settings.ECOS_API_KEY:
            raise RuntimeError(
                "ECOS_API_KEY 가 없습니다. docs/SETUP_KEYS.md 를 참고하세요.")

        frames: list[pd.DataFrame] = []
        for item in settings.series_for("ecos"):
            key, stat = item["key"], item["stat_code"]
            code, cycle = item["item_code"], item["cycle"]
            if "TBD" in (stat, code):
                print(f"  [{key}] 코드 미확정(TBD) — 건너뜀")
                continue

            try:
                s = fetch_series(stat, cycle, code, start, end,
                                 str(item.get("item_code2", "") or ""))
            except Exception as e:
                # 한 시리즈의 실패가 전체를 죽이지 않는다 (설계원칙 5)
                print(f"  [{key}] FAIL {type(e).__name__}: {e}")
                continue
            if s.empty:
                print(f"  [{key}] 데이터 없음")
                continue

            scaled = s.to_numpy(dtype="float64") * float(item.get("scale", 1.0))
            _check_range(key, scaled, item)

            frames.append(pd.DataFrame({
                "date": s.index,
                "series_id": f"ecos.{key}",
                "value": scaled,
            }))
            print(f"  [{key}] {item['name']} {len(s)}행  "
                  f"{s.index.min().date()}~{s.index.max().date()}  "
                  f"last={scaled[-1]:,.3f} {item.get('unit_out', '')}")

        if not frames:
            return pd.DataFrame(columns=["date", "series_id", "value"])
        return pd.concat(frames, ignore_index=True)


def _check_range(key: str, scaled, item: dict) -> None:
    """단위 정규화 결과가 상식 범위인지 확인 (§7.4 와 같은 사고 방지)."""
    rng = item.get("expect_range")
    if not rng:
        return
    lo, hi = float(rng[0]), float(rng[1])
    vmin, vmax = float(scaled.min()), float(scaled.max())
    if vmin < lo or vmax > hi:
        print(f"  !! [{key}] 범위 이탈: [{vmin:,.3f}, {vmax:,.3f}] "
              f"⊄ [{lo:,.0f}, {hi:,.0f}] {item.get('unit_out', '')} "
              f"— series.yaml 의 scale 을 의심하세요")


if __name__ == "__main__":
    EcosFetcher().run()
