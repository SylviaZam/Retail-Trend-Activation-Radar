#!/usr/bin/env python3
"""Retail Trend & Activation Radar (CLI).

Pulls Google Trends for each market in config/markets.json, using the word
people in that market actually search for, and writes:

- data/trends_signals.csv         weekly signals (trend, MA, YoY, z-score) per market x concept
- data/localization_pairs.csv     local term vs English term, same request
- reports/activation_radar.xlsx   Readme, Market_Peaks, Seasonality, Localization, Activation_Radar, Top_Months
- assets/*.png                    seasonality, activation radar, localization charts

Usage:
  python -m src.radar_cli                          # all markets in config/markets.json
  python -m src.radar_cli --only US,JP             # a subset
  python -m src.radar_cli --geo MX --keywords tenis,muebles   # quick single-market run

Exit codes: 0 ok, 2 bad input, 75 Google Trends unavailable (previous outputs left untouched).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from . import radar, report
from .trends import TrendsClient, TrendsUnavailable

EX_TEMPFAIL = 75


def load_markets(path: Path, only: str | None = None) -> dict:
    markets = json.loads(Path(path).read_text(encoding="utf-8"))["markets"]
    if only:
        wanted = [m.strip().upper() for m in only.split(",") if m.strip()]
        missing = [m for m in wanted if m not in markets]
        if missing:
            raise SystemExit(f"ERROR: markets not in config: {', '.join(missing)}")
        markets = {m: markets[m] for m in wanted}
    return markets


def single_market(geo: str, keywords: str) -> dict:
    terms = [k.strip() for k in keywords.split(",") if k.strip()]
    if not terms:
        raise SystemExit("ERROR: provide at least one keyword via --keywords")
    return {geo.upper(): {"name": geo.upper(), "hl": "en-US", "terms": {t: t for t in terms}}}


def fetch_markets(client, markets: dict) -> pd.DataFrame:
    """One request per term. Google scales each request to its biggest term, so
    batching would squash smaller terms to 1-3 points and blur their seasonal shape."""
    frames = []
    for geo, cfg in markets.items():
        for concept, term in cfg["terms"].items():
            print(f"[INFO] {geo}: {term}")
            df = client.interest([term], geo=geo, hl=cfg.get("hl", "en-US"))
            if df.empty:
                continue
            df["market"], df["concept"] = geo, concept
            frames.append(df)
    if not frames:
        raise TrendsUnavailable("Google Trends returned no data")
    return pd.concat(frames, ignore_index=True)


def fetch_localization(client, markets: dict) -> pd.DataFrame:
    """For each concept whose local term differs from English, request both together."""
    rows = []
    for geo, cfg in markets.items():
        for concept, term in cfg["terms"].items():
            if term == concept:
                continue
            print(f"[INFO] {geo}: '{term}' vs '{concept}'")
            df = client.interest([term, concept], geo=geo, hl=cfg.get("hl", "en-US"))
            if df.empty:
                continue
            wide = df.pivot_table(index="date", columns="term", values="trend").reset_index()
            rows.append(pd.DataFrame({
                "market": geo, "concept": concept, "date": wide["date"],
                "local_term": term, "english_term": concept,
                "local": wide.get(term, 0), "english": wide.get(concept, 0),
            }))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def build(raw: pd.DataFrame, pairs: pd.DataFrame, root: Path, out: Path) -> dict:
    """Compute every table and write all outputs under `root`. Returns the tables."""
    signals = radar.add_signal_features(raw)
    scored, top_months, pivot = radar.activation_score(radar.monthly_agg(signals))
    season = radar.seasonality(raw)
    peaks = radar.peak_table(season)
    loc = radar.localization_share(pairs) if not pairs.empty else pd.DataFrame()

    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    signals.to_csv(root / "data" / "trends_signals.csv", index=False)
    if not pairs.empty:
        pairs.to_csv(root / "data" / "localization_pairs.csv", index=False)

    report.export_excel(root / out, signals, scored, top_months, season, peaks, loc)
    if not season.empty:
        report.plot_seasonality(season, root / "assets" / "market_seasonality.png")
    report.plot_activation(pivot, root / "assets" / "activation_radar.png")
    if not loc.empty:
        report.plot_localization(loc, root / "assets" / "localization_gap.png")
    return {"signals": signals, "scored": scored, "top_months": top_months,
            "season": season, "peaks": peaks, "localization": loc}


def main(argv=None, client=None) -> int:
    ap = argparse.ArgumentParser(description="Generate the Retail Trend & Activation Radar.")
    ap.add_argument("--markets", default="config/markets.json", help="Market and keyword config (JSON).")
    ap.add_argument("--only", help="Comma-separated subset of market codes, e.g. US,JP.")
    ap.add_argument("--geo", help="Quick single-market run: country code (use with --keywords).")
    ap.add_argument("--keywords", help="Quick single-market run: comma-separated search terms.")
    ap.add_argument("--timeframe", default="today 5-y", help='Google Trends window, e.g. "today 5-y".')
    ap.add_argument("--skip-localization", action="store_true", help="Skip the local-vs-English comparison.")
    ap.add_argument("--out", default="reports/activation_radar.xlsx", help="Excel path.")
    ap.add_argument("--root", default=".", help="Folder to write data/, reports/, assets/ into.")
    args = ap.parse_args(argv)

    markets = single_market(args.geo or "US", args.keywords) if args.keywords else load_markets(args.markets, args.only)
    client = client or TrendsClient(timeframe=args.timeframe)
    try:
        raw = fetch_markets(client, markets)
        pairs = pd.DataFrame() if args.skip_localization else fetch_localization(client, markets)
    except TrendsUnavailable as exc:
        print(f"[SKIP] Google Trends unavailable ({exc}). Previous outputs left unchanged.", file=sys.stderr)
        return EX_TEMPFAIL

    tables = build(raw, pairs, Path(args.root), Path(args.out))
    print(f"[OK] {raw['market'].nunique()} markets, {raw['concept'].nunique()} concepts, "
          f"{raw['date'].nunique()} weeks -> {Path(args.root) / args.out}")
    if not tables["peaks"].empty:
        print(tables["peaks"].to_string(index=False))
    if not tables["localization"].empty:
        print(tables["localization"].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
