"""로컬 data/ 가 원격보다 낡았는지 검사한다.

**왜 필요한가.** GitHub Actions 가 data/ 를 커밋하는데, 로컬에서 작업하다
rebase 충돌이 나면 parquet 은 텍스트 병합이 안 되므로 한쪽을 골라야 한다.
그때 잘못 고르면 **서버가 받아둔 최신 데이터를 조용히 되돌린다.**

실제로 그렇게 됐다 — rebase 중 `--theirs` 를 골랐는데, rebase 에서
`--theirs` 는 '적용 중인 커밋(내 것, 오래된 것)'이고 서버의 새 데이터는
`--ours` 쪽이었다. 대시보드가 사흘 낡은 KOSPI 를 보여주고 있었다.

  rebase 중 conflict:  --ours = 이미 적용된 쪽(원격),  --theirs = 지금 얹는 쪽(내 것)
  merge 중 conflict :  --ours = 현재 브랜치,          --theirs = 병합 대상

헷갈리기 쉬우므로 **고르지 말고 ETL 을 다시 돌리는 편이 안전하다.**

    python scripts/check_data_freshness.py
    python scripts/check_data_freshness.py --ref origin/main
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: (파일, 날짜컬럼) — 최신 관측일을 비교할 대상
TARGETS = ["macro", "prices", "flows", "vintages"]


def max_date(df: pd.DataFrame) -> pd.Timestamp | None:
    if df.empty or "date" not in df.columns:
        return None
    return pd.Timestamp(df["date"].max())


def ref_version(name: str, ref: str) -> pd.DataFrame:
    """git ref 시점의 parquet 을 읽는다."""
    path = f"data/{name}.parquet"
    try:
        blob = subprocess.run(["git", "show", f"{ref}:{path}"],
                              cwd=ROOT, capture_output=True, check=True).stdout
    except subprocess.CalledProcessError:
        return pd.DataFrame()
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
        f.write(blob)
        tmp = f.name
    try:
        return pd.read_parquet(tmp)
    except Exception:
        return pd.DataFrame()
    finally:
        Path(tmp).unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="origin/main",
                    help="비교 대상 git ref (기본 origin/main)")
    a = ap.parse_args()

    subprocess.run(["git", "fetch", "-q", "origin"], cwd=ROOT, check=False)

    from src import store

    stale = []
    print(f"로컬 data/ vs {a.ref}\n")
    for name in TARGETS:
        local = max_date(store.read(name))
        remote = max_date(ref_version(name, a.ref))
        if local is None and remote is None:
            continue
        mark = "OK"
        if remote is not None and (local is None or local < remote):
            mark = "★ 낡음"
            stale.append((name, local, remote))
        print(f"  {name:10} 로컬 {str(local.date()) if local is not None else '-':>12}"
              f"   원격 {str(remote.date()) if remote is not None else '-':>12}   {mark}")

    if not stale:
        print("\n로컬이 원격보다 낡지 않았습니다.")
        return 0

    print("\n★ 로컬 데이터가 원격보다 오래됐습니다. 그대로 커밋하면 "
          "서버가 받아둔 데이터를 되돌립니다.")
    print("  해결: python -m src.etl.run_all   (골라 덮지 말고 다시 수집)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
