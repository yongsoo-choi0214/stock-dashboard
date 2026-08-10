"""미국/글로벌 지수 — yfinance (CLAUDE.md §2 us_equity.py).

키가 필요 없다. Phase 1에서 확인한 원시 응답 형태:
  yf.download(tickers, group_by="ticker") → MultiIndex 컬럼 (ticker, price)
  index dtype = datetime64[ns], tz-naive
  ^VIX / DX-Y.NYB 는 volume 이 항상 0
"""
from __future__ import annotations

import pandas as pd

from config import settings
from src.etl.base import Fetcher, retry

COLMAP = {"Open": "open", "High": "high", "Low": "low",
          "Close": "close", "Volume": "volume"}


@retry(times=3)
def _download(tickers: list[str], start: pd.Timestamp,
              end: pd.Timestamp) -> pd.DataFrame:
    import yfinance as yf

    return yf.download(
        tickers,
        start=start.date().isoformat(),
        # yfinance 의 end 는 배타적(exclusive)이라 하루 더한다
        end=(end + pd.Timedelta(days=1)).date().isoformat(),
        auto_adjust=False,
        progress=False,
        group_by="ticker",
        threads=False,
    )


class UsEquityFetcher(Fetcher):
    source, target, keys = "us_equity", "prices", ["date", "ticker"]
    # 4개 티커를 한 번에 받으므로 전체 재수집이 싸다
    full_refresh = True

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        items = settings.series_for("yfinance")
        tickers = [i["ticker"] for i in items]
        names = {i["ticker"]: i["name"] for i in items}

        raw = _download(tickers, start, end)
        if raw.empty:
            print("  yfinance 응답 없음")
            return pd.DataFrame(columns=["date", "ticker", *COLMAP.values()])

        frames: list[pd.DataFrame] = []
        for tk in tickers:
            try:
                # 티커 1개면 MultiIndex 가 아닐 수 있다
                sub = raw[tk] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                print(f"  [{tk}] 응답에 없음 — 건너뜀")
                continue

            sub = sub.loc[:, [c for c in COLMAP if c in sub.columns]]
            sub = sub.rename(columns=COLMAP).dropna(subset=["close"])
            if sub.empty:
                print(f"  [{tk}] 데이터 없음")
                continue

            idx = pd.to_datetime(sub.index)
            if isinstance(idx.dtype, pd.DatetimeTZDtype):
                idx = idx.tz_localize(None)

            df = sub.reset_index(drop=True)
            df.insert(0, "ticker", f"YF.{tk}")
            df.insert(0, "date", idx.normalize())
            for c in COLMAP.values():
                if c not in df.columns:
                    df[c] = pd.NA
            frames.append(df)
            print(f"  [YF.{tk}] {names.get(tk, '')} {len(df)}행  "
                  f"{df['date'].min().date()}~{df['date'].max().date()}  "
                  f"last close={df['close'].iloc[-1]:,.2f}")

        if not frames:
            return pd.DataFrame(columns=["date", "ticker", *COLMAP.values()])
        return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    UsEquityFetcher().run()
