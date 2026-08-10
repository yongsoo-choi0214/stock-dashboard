"""한국 지수/수급 (CLAUDE.md §5.5).

★ 명세와의 차이 — pykrx 1.2+ 는 data.krx.co.kr 로그인을 요구한다.
   Phase 1에서 공개 JSON 엔드포인트를 직접 호출해도 `400 LOGOUT` 이 돌아오는 것을
   확인했다. 클라이언트 문제가 아니라 서버 측 강제다.

   - 지수 OHLCV : FinanceDataReader 로 대체 (인증 불필요). ticker 는 §3.2 계약대로
                  `KRX.{code}` 를 유지하고 내부에서만 FDR 심볼로 매핑한다.
   - 투자자 수급 : 대체 경로가 없다. KRX_ID/KRX_PW 가 있어야 동작한다.
"""
from __future__ import annotations

import os
import time

import pandas as pd

from config import settings
from src.etl.base import Fetcher, retry

# §3.2 의 KRX 코드 → FinanceDataReader 심볼
FDR_SYMBOL = {"1001": "KS11", "2001": "KQ11", "1028": "KS200"}

COLMAP = {"Open": "open", "High": "high", "Low": "low",
          "Close": "close", "Volume": "volume"}


@retry(times=3)
def _fdr_read(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    import FinanceDataReader as fdr

    return fdr.DataReader(symbol, start.date().isoformat(), end.date().isoformat())


class KrxIndexFetcher(Fetcher):
    source, target, keys = "krx_index", "prices", ["date", "ticker"]
    full_refresh = True

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for item in settings.series_for("krx_index"):
            code = str(item["ticker"])
            symbol = FDR_SYMBOL.get(code)
            if symbol is None:
                print(f"  [{code}] FDR 심볼 매핑 없음 — 건너뜀")
                continue
            try:
                raw = _fdr_read(symbol, start, end)
            except Exception as e:
                # 한 지수의 실패가 전체를 죽이지 않는다 (설계원칙 5)
                print(f"  [{code}] FAIL {type(e).__name__}: {e}")
                continue
            if raw.empty:
                print(f"  [{code}] 데이터 없음")
                continue

            sub = raw.loc[:, [c for c in COLMAP if c in raw.columns]]
            sub = sub.rename(columns=COLMAP).dropna(subset=["close"])
            idx = pd.to_datetime(sub.index)
            if isinstance(idx.dtype, pd.DatetimeTZDtype):
                idx = idx.tz_localize(None)

            df = sub.reset_index(drop=True)
            df.insert(0, "ticker", f"KRX.{code}")
            df.insert(0, "date", idx.normalize())
            frames.append(df)
            print(f"  [KRX.{code}] {item['name']} ({symbol}) {len(df)}행  "
                  f"{df['date'].min().date()}~{df['date'].max().date()}  "
                  f"last close={df['close'].iloc[-1]:,.2f}")
            time.sleep(settings.KRX_SLEEP)   # §7.5 과다 요청 방지

        if not frames:
            return pd.DataFrame(columns=["date", "ticker", *COLMAP.values()])
        return pd.concat(frames, ignore_index=True)


class KrxFlowFetcher(Fetcher):
    """투자자별 순매수 대금. KRX_ID/KRX_PW 필요."""

    source, target, keys = "krx_flow", "flows", ["date", "market", "investor"]

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
            raise RuntimeError(
                "KRX_ID/KRX_PW 가 없어 투자자 수급을 받을 수 없습니다. "
                "data.krx.co.kr 회원가입 후 .env 에 기록하세요 (docs/SETUP_KEYS.md)."
            )
        from pykrx import stock

        frames: list[pd.DataFrame] = []
        for market in ("KOSPI", "KOSDAQ"):
            # 장기 구간은 1년 단위로 쪼개 요청한다 (§7.5)
            for s, e in _yearly_chunks(start, end):
                raw = stock.get_market_trading_value_by_investor(
                    s.strftime("%Y%m%d"), e.strftime("%Y%m%d"), market
                )
                time.sleep(settings.KRX_SLEEP)
                if raw is None or raw.empty:
                    continue
                # 인덱스=투자자, '순매수' 컬럼을 사용. 구간 합계이므로 종료일에 귀속.
                col = "순매수" if "순매수" in raw.columns else raw.columns[-1]
                frames.append(pd.DataFrame({
                    "date": e.normalize(),
                    "market": market,
                    "investor": raw.index.astype(str),
                    "net_value": pd.to_numeric(raw[col], errors="coerce"),
                }))

        if not frames:
            return pd.DataFrame(
                columns=["date", "market", "investor", "net_value"])
        return pd.concat(frames, ignore_index=True)


def _yearly_chunks(start: pd.Timestamp, end: pd.Timestamp):
    cur = start
    while cur <= end:
        nxt = min(cur + pd.DateOffset(years=1) - pd.Timedelta(days=1), end)
        yield cur, nxt
        cur = nxt + pd.Timedelta(days=1)


if __name__ == "__main__":
    KrxIndexFetcher().run()
