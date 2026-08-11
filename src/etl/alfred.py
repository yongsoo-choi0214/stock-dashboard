"""ALFRED — FRED 의 시점별 아카이브(vintage) 수집.

**왜 필요한가.** 지금 macro.parquet 의 FRED 값은 '오늘 시점에서 본 과거'다.
2008년 WALCL 은 그 뒤 여러 번 손질된 최신판이지, 2008년 당시 화면에 떠 있던
값이 아니다. 그 상태로 백테스트하면 미래 정보가 새어든다.

ALFRED 는 **최초 발표치와 그 발표일**을 준다. 백테스트의 시간축은 관측일이
아니라 공표일(available_from)이어야 한다.

    GET fred/series/observations?output_type=4
        &realtime_start=1776-07-04&realtime_end=9999-12-31
    → {realtime_start: 공표일, date: 관측일, value: 최초 발표값}

**한계**: 시리즈마다 아카이브 시작일이 다르다. 그 이전 구간은 시점 데이터가
아예 없다(예: RRPONTSYD 는 2016-03-28 부터). 없는 구간을 현재값으로 메우면
원래 문제로 돌아가므로, 이 모듈은 **없으면 없는 대로 둔다**.
"""
from __future__ import annotations

import sys

import pandas as pd
import requests

# Windows 콘솔 기본 코덱이 cp949라 진행 로그의 한글·em-dash 에서 죽는다.
# 로그 한 줄 때문에 수집이 끊기면 안 된다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import settings
from src.etl.base import Fetcher, retry

API = "https://api.stlouisfed.org/fred/series/observations"
VINTAGE_API = "https://api.stlouisfed.org/fred/series/vintagedates"

#: 시점 데이터가 필요한 시리즈 — 순유동성 3종.
#: 나머지(금리·스프레드)는 사실상 정정되지 않아 우선순위가 낮다.
TARGETS = ["WALCL", "WTREGEN", "RRPONTSYD"]


#: FRED 는 요청 하나에 포함되는 vintage 개수를 2,000 으로 제한한다.
#: RRPONTSYD 처럼 일간이면 2,576개라 한 번에 못 받는다 → 실시간 구간을 쪼갠다.
MAX_VINTAGES = 1800


def _scrub(msg: str) -> str:
    """에러 메시지에 박혀 나오는 API 키를 지운다.

    requests 의 HTTPError 는 전체 URL 을 담고, 거기 api_key 가 그대로 들어간다.
    로그·티켓·화면 어디로 흘러갈지 모르므로 발생 지점에서 지운다.
    """
    key = settings.FRED_API_KEY
    return msg.replace(key, "***") if key else msg


@retry(times=3)
def vintage_dates(series_id: str) -> list[pd.Timestamp]:
    """이 시리즈의 아카이브 날짜 전체."""
    try:
        r = requests.get(VINTAGE_API, timeout=30, params={
            "series_id": series_id, "api_key": settings.FRED_API_KEY,
            "file_type": "json"})
        r.raise_for_status()
    except requests.HTTPError as e:
        raise requests.HTTPError(_scrub(str(e))) from None
    return [pd.Timestamp(d) for d in r.json().get("vintage_dates", [])]


def vintage_range(series_id: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    v = vintage_dates(series_id)
    return (v[0], v[-1]) if v else (None, None)


@retry(times=3)
def _fetch_window(series_id: str, start: pd.Timestamp, end: pd.Timestamp,
                  rt_start: pd.Timestamp, rt_end: pd.Timestamp) -> list[dict]:
    try:
        r = requests.get(API, timeout=60, params={
            "series_id": series_id, "api_key": settings.FRED_API_KEY,
            "file_type": "json", "output_type": 4,
            "realtime_start": rt_start.date().isoformat(),
            "realtime_end": rt_end.date().isoformat(),
            "observation_start": start.date().isoformat(),
            "observation_end": end.date().isoformat()})
        r.raise_for_status()
    except requests.HTTPError as e:
        raise requests.HTTPError(_scrub(str(e))) from None
    return r.json().get("observations", [])


def initial_releases(series_id: str, start: pd.Timestamp,
                     end: pd.Timestamp) -> pd.DataFrame:
    """최초 발표치와 공표일. [date, value, available_from]"""
    vd = vintage_dates(series_id)
    if not vd:
        return pd.DataFrame(columns=["date", "value", "available_from"])

    # vintage 2,000개 제한 → 실시간 구간을 나눠 요청한다
    obs: list[dict] = []
    for i in range(0, len(vd), MAX_VINTAGES):
        chunk = vd[i:i + MAX_VINTAGES]
        obs.extend(_fetch_window(series_id, start, end, chunk[0], chunk[-1]))
    if not obs:
        return pd.DataFrame(columns=["date", "value", "available_from"])

    df = pd.DataFrame(obs)
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]),
        "value": pd.to_numeric(df["value"], errors="coerce"),
        "available_from": pd.to_datetime(df["realtime_start"]),
    }).dropna(subset=["value"])
    # 구간을 나눠 받았으므로 경계에서 중복이 생길 수 있다
    out = out.drop_duplicates(subset=["date"], keep="first")
    # 아카이브 시작 전 관측치는 realtime_start 가 아카이브 개시일로 찍힌다.
    # 그건 '그날 발표됐다'는 뜻이 아니므로 시점 데이터로 쓰면 안 된다.
    first_vintage = vd[0]
    if first_vintage is not None:
        bogus = out["available_from"] <= first_vintage
        if bogus.any():
            print(f"  [{series_id}] 아카이브 개시({first_vintage.date()}) 이전 "
                  f"{int(bogus.sum())}건 제외 — 실제 공표일을 알 수 없음")
            out = out[~bogus]
    return out.sort_values("date").reset_index(drop=True)


class AlfredFetcher(Fetcher):
    source, target, keys = "alfred", "vintages", ["date", "series_id"]
    full_refresh = True   # 3개 시리즈 × 1회 호출. 싸다.

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        if not settings.FRED_API_KEY:
            raise RuntimeError(
                "ALFRED 는 FRED_API_KEY 가 필요합니다 (무키 CSV 경로 없음).")

        scale = {i["id"]: float(i["scale"]) for i in settings.series_for("fred")}
        frames = []
        for sid in TARGETS:
            try:
                df = initial_releases(sid, start, end)
            except Exception as e:
                print(f"  [{sid}] FAIL {type(e).__name__}: {e}")
                continue
            if df.empty:
                print(f"  [{sid}] 시점 데이터 없음")
                continue

            df["value"] *= scale.get(sid, 1.0)   # macro 와 같은 단위로 맞춘다
            df["series_id"] = f"fred.{sid}"
            lag = (df["available_from"] - df["date"]).dt.days
            print(f"  [{sid}] {len(df)}행  {df['date'].min().date()}~"
                  f"{df['date'].max().date()}  공표시차 중앙값 {lag.median():.0f}일")
            frames.append(df[["date", "series_id", "value", "available_from"]])

        if not frames:
            return pd.DataFrame(
                columns=["date", "series_id", "value", "available_from"])
        return pd.concat(frames, ignore_index=True)


# ------------------------------------------------------------------ 사용
def point_in_time(vintages: pd.DataFrame, series_id: str) -> pd.Series:
    """공표일을 시간축으로 하는 시계열.

    index = 그 값을 알 수 있게 된 날. 백테스트에서 t 시점에 참조해도
    미래를 보지 않는다.
    """
    df = vintages[vintages["series_id"] == series_id]
    if df.empty:
        return pd.Series(dtype="float64")
    # 같은 날 여러 관측치가 공표되면 가장 최근 관측일의 값을 쓴다
    df = df.sort_values(["available_from", "date"])
    s = df.set_index("available_from")["value"]
    return s[~s.index.duplicated(keep="last")].sort_index()


if __name__ == "__main__":
    AlfredFetcher().run()
