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
    """투자자별 **일간** 순매수 대금(원). KRX_ID/KRX_PW 필요.

    ★ get_market_trading_value_by_**investor** 를 쓰면 안 된다.
      그쪽은 요청 구간 전체를 하나로 합산해 13개 투자자 × 1행만 돌려준다.
      1년 단위로 잘라 호출하면 '연 1행'짜리 시계열이 되어 쓸모가 없다.
      by_**date** 가 날짜별 행을 준다. 컬럼이 곧 투자자다.

    full_refresh 를 켜지 않는 이유: 스크래핑이라 호출 비용이 크다(§7.5).
    lookback 재수집으로 최근 구간만 갱신한다.
    """

    source, target, keys = "krx_flow", "flows", ["date", "market", "investor"]

    # '전체'는 매수-매도 합이라 항상 0이다. 저장할 이유가 없다.
    DROP_COLS = {"전체"}

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
            raise RuntimeError(
                "KRX_ID/KRX_PW 가 없어 투자자 수급을 받을 수 없습니다. "
                "data.krx.co.kr 회원가입 후 .env 에 기록하세요 (docs/SETUP_KEYS.md)."
            )
        from pykrx import stock

        frames: list[pd.DataFrame] = []
        for market in ("KOSPI", "KOSDAQ"):
            got = 0
            # 장기 구간은 1년 단위로 쪼개 요청한다 (§7.5)
            for s, e in _yearly_chunks(start, end):
                try:
                    raw = stock.get_market_trading_value_by_date(
                        s.strftime("%Y%m%d"), e.strftime("%Y%m%d"), market)
                except Exception as ex:
                    print(f"  [{market}] {s.date()}~{e.date()} "
                          f"FAIL {type(ex).__name__}: {ex}")
                    continue
                finally:
                    time.sleep(settings.KRX_SLEEP)

                if raw is None or raw.empty:
                    continue

                cols = [c for c in raw.columns if c not in self.DROP_COLS]
                # 와이드(날짜×투자자) → 롱포맷 (§3.3)
                long = (raw[cols]
                        .rename_axis("date").reset_index()
                        .melt(id_vars="date", var_name="investor",
                              value_name="net_value"))
                long["market"] = market
                long["net_value"] = pd.to_numeric(long["net_value"],
                                                  errors="coerce")
                long = long.dropna(subset=["net_value"])
                frames.append(long)
                got += len(long)

            print(f"  [{market}] {got}행")

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


class KrxAuxFetcher(Fetcher):
    """지수 부가 정보 — 시가총액·거래대금을 macro 에 넣는다.

    ECOS 802Y001 은 유가증권시장 시총만 준다. 코스닥 시총이 없어서
    코스닥 취약성 지수를 만들 수 없었다. FDR 은 두 시장 모두 2005년부터 준다.

    금액은 **조원**으로 통일한다 — ECOS 계열과 같은 단위여야 지표가 성립한다.
    """

    source, target, keys = "krx_aux", "macro", ["date", "series_id"]
    full_refresh = True

    #: FDR 컬럼 → (series_id 접미사, 원 단위 → 조원 배수)
    FIELDS = {"MarCap": ("marcap", 1e-12), "Amount": ("amount", 1e-12)}

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for item in settings.series_for("krx_index"):
            code = str(item["ticker"])
            symbol = FDR_SYMBOL.get(code)
            if symbol is None:
                continue
            try:
                raw = _fdr_read(symbol, start, end)
            except Exception as e:
                print(f"  [{code}] FAIL {type(e).__name__}: {e}")
                continue
            if raw.empty:
                continue

            idx = pd.to_datetime(raw.index)
            if isinstance(idx.dtype, pd.DatetimeTZDtype):
                idx = idx.tz_localize(None)

            for col, (suffix, scale) in self.FIELDS.items():
                if col not in raw.columns:
                    continue
                s = pd.to_numeric(raw[col], errors="coerce").dropna()
                if s.empty:
                    continue
                frames.append(pd.DataFrame({
                    "date": idx[raw.index.isin(s.index)].normalize(),
                    "series_id": f"krx.{code}_{suffix}",
                    "value": s.to_numpy(dtype="float64") * scale,
                }))
                print(f"  [krx.{code}_{suffix}] {len(s)}행  "
                      f"last={s.iloc[-1] * scale:,.1f} 조원")
            time.sleep(settings.KRX_SLEEP)

        if not frames:
            return pd.DataFrame(columns=["date", "series_id", "value"])
        return pd.concat(frames, ignore_index=True)
