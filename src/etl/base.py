"""Fetcher 추상 인터페이스 + 재시도 + 증분 로직 (CLAUDE.md §5.2)."""
from __future__ import annotations

import functools
import time
from abc import ABC, abstractmethod

import pandas as pd

from config.settings import DEFAULT_START, LOOKBACK_DAYS
from src import store


def retry(times: int = 3, backoff: float = 2.0):
    """지수 백오프 재시도 데코레이터. 마지막 시도 실패 시 예외를 그대로 올린다."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            delay = 1.0
            for attempt in range(1, times + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    if attempt == times:
                        raise
                    print(f"  retry {attempt}/{times - 1} "
                          f"({type(e).__name__}: {e}) — {delay:.1f}s 후 재시도")
                    time.sleep(delay)
                    delay *= backoff

        return wrapper

    return deco


class Fetcher(ABC):
    source: str          # "fred" | "ecos" | "krx" | "us_equity"
    target: str          # "macro" | "prices" | "flows"
    keys: list[str]      # upsert 키

    #: True 면 매 실행마다 DEFAULT_START 부터 전체를 다시 받는다.
    #: 호출 비용이 싼 소스(FRED CSV 7건)는 이쪽이 낫다 — 정정분을 100% 반영하고
    #: 실행 결과가 항상 동일해 멱등성이 자명해진다.
    #: KRX 처럼 스크래핑 비용이 큰 소스만 False 로 두고 lookback 증분을 쓴다.
    full_refresh: bool = False

    @abstractmethod
    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """원시 수집 → §3 스키마로 정규화하여 반환. 저장은 하지 않는다."""

    def start_from(self, lookback_days: int) -> pd.Timestamp:
        """마지막 관측일 - lookback. 데이터가 없으면 DEFAULT_START.

        lookback을 두는 이유: FRED·ECOS 값은 나중에 정정된다.
        마지막 날짜 이후만 받으면 과거 정정분을 영원히 놓친다.

        주의: 여기서 보는 last_date 는 target 테이블 전체의 최대일이다.
        발표 지연이 긴 계열(M2 등)은 이 구간에 관측치가 없을 수 있으므로,
        그런 소스는 full_refresh 를 쓰거나 fetch() 내부에서 계열별로 판단해야 한다.
        """
        if self.full_refresh:
            return pd.Timestamp(DEFAULT_START)
        last = store.last_date(self.target)
        if last is None:
            return pd.Timestamp(DEFAULT_START)
        return last - pd.Timedelta(days=lookback_days)

    def run(self, lookback_days: int = LOOKBACK_DAYS) -> pd.DataFrame:
        """last_date - lookback_days 부터 재수집 후 upsert."""
        start = self.start_from(lookback_days)
        end = pd.Timestamp.today().normalize()
        print(f"[{self.source}] fetch {start.date()} → {end.date()}")

        df = self.fetch(start, end)
        if df.empty:
            print(f"[{self.source}] 신규 데이터 없음")
            store.update_meta(self.source, "ok", rows=0, note="no new data")
            return store.read(self.target)

        full = store.upsert(self.target, df, self.keys)
        max_date = pd.Timestamp(df["date"].max()).date().isoformat()
        print(f"[{self.source}] {len(df)}행 수집, max_date={max_date}, "
              f"{self.target} 총 {len(full)}행")
        store.update_meta(self.source, "ok", rows=len(df),
                          max_date=max_date, target_rows=len(full))
        return full
