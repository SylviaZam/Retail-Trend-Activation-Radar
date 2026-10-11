"""Offline tests: synthetic weekly series with known answers. No network."""
import numpy as np
import pandas as pd
import pytest

from src import radar
from src.trends import TrendsUnavailable, with_retries


def weekly(market="US", concept="sneakers", years=4, peak_month=None, growth=1.0, base=50.0):
    dates = pd.date_range("2021-01-03", periods=52 * years, freq="W-SUN")
    trend = base * growth ** (np.arange(len(dates)) / 52)
    if peak_month:
        trend = np.where(dates.month == peak_month, trend * 1.5, trend)
    return pd.DataFrame({"market": market, "concept": concept, "term": concept, "date": dates, "trend": trend})


def test_tidy_interest_drops_partial_week():
    raw = pd.DataFrame({"sneakers": [10, 20, 5], "isPartial": [False, False, True]},
                       index=pd.to_datetime(["2026-09-20", "2026-09-27", "2026-10-04"]))
    out = radar.tidy_interest(raw)
    assert list(out["trend"]) == [10, 20]
    assert set(out.columns) == {"date", "term", "trend"}


def test_yoy_index_reads_200_when_interest_doubles():
    df = weekly(growth=2.0)
    out = radar.add_signal_features(df)
    assert out["yoy_idx"].iloc[:52].isna().all()
    assert out["yoy_idx"].iloc[60] == pytest.approx(200, rel=1e-6)


def test_flat_series_has_no_z_score_instead_of_dividing_by_zero():
    out = radar.add_signal_features(weekly())
    assert out["z_score"].isna().all()


def test_monthly_agg_counts_weeks_and_hot_share_is_a_fraction():
    agg = radar.monthly_agg(radar.add_signal_features(weekly(peak_month=11)))
    assert agg["weeks"].between(4, 5).all()
    assert agg["hot_share"].between(0, 1).all()


def test_activation_score_is_bounded_and_keeps_top_three_per_series():
    df = pd.concat([weekly(peak_month=11), weekly(market="JP", peak_month=3)])
    scored, top, pivot = radar.activation_score(radar.monthly_agg(radar.add_signal_features(df)))
    assert scored["act_score"].between(0, 1).all()
    assert top.groupby(["market", "concept"]).size().eq(3).all()
    assert set(pivot.index) == {"US · sneakers", "JP · sneakers"}


def test_seasonality_finds_the_peak_month_and_ignores_growth():
    df = pd.concat([weekly(peak_month=11, growth=1.3), weekly(market="JP", peak_month=3)])
    season = radar.seasonality(df)
    peaks = radar.peak_table(season).set_index("market")
    assert peaks.loc["US", "peak_month"] == "Nov"
    assert peaks.loc["JP", "peak_month"] == "Mar"
    # Average month is ~100 once growth is removed year by year.
    assert season.groupby("market")["season_idx"].mean().between(98, 102).all()


def test_seasonality_uses_complete_years_only():
    df = weekly(years=1)
    df = df[df["date"].dt.month > 2]  # an incomplete year
    assert radar.seasonality(df).empty


def test_localization_share():
    pairs = pd.DataFrame({"market": "JP", "concept": "sneakers", "local_term": "スニーカー",
                          "english_term": "sneakers", "date": pd.date_range("2026-01-04", periods=4, freq="W"),
                          "local": [90, 98, 94, 98], "english": [1, 1, 2, 0]})
    out = radar.localization_share(pairs)
    assert out.loc[0, "local_share"] == pytest.approx(95 / 96, abs=1e-3)


class Fake429(Exception):
    pass


def test_retries_rate_limits_then_succeeds():
    calls, waits = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise Fake429("The request failed: Google returned a response with code 429")
        return pd.DataFrame({"ok": [1]})

    out = with_retries(flaky, sleep=waits.append)
    assert len(calls) == 3 and waits == [20.0, 40.0]
    assert out["ok"].iloc[0] == 1


def test_retries_give_up_with_a_clear_error():
    def always_429():
        raise Fake429("code 429")

    with pytest.raises(TrendsUnavailable):
        with_retries(always_429, attempts=2, sleep=lambda s: None)
