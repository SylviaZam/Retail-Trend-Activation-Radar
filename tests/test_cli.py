"""End-to-end CLI run against a fake Google Trends client."""
import numpy as np
import pandas as pd
from openpyxl import load_workbook

from src import radar_cli
from src.trends import TrendsUnavailable


class FakeClient:
    """Returns deterministic weekly series; the local word always beats English."""

    def interest(self, terms, geo, hl="en-US"):
        dates = pd.date_range("2021-01-03", periods=52 * 4, freq="W-SUN")
        rows = []
        for k, term in enumerate(terms):
            ascii_term = term.isascii()
            base = 10 if (ascii_term and geo == "JP") else 60
            season = 1 + 0.4 * (dates.month == (11 if geo == "US" else 3 + k))
            rows.append(pd.DataFrame({"date": dates, "term": term, "trend": np.round(base * season)}))
        return pd.concat(rows, ignore_index=True)


class DownClient:
    def interest(self, terms, geo, hl="en-US"):
        raise TrendsUnavailable("code 429")


def test_full_run_writes_every_output(tmp_path):
    code = radar_cli.main(["--only", "US,JP", "--root", str(tmp_path)], client=FakeClient())
    assert code == 0
    for rel in ["data/trends_signals.csv", "data/localization_pairs.csv", "reports/activation_radar.xlsx",
                "assets/market_seasonality.png", "assets/activation_radar.png", "assets/localization_gap.png"]:
        assert (tmp_path / rel).stat().st_size > 0, rel
    wb = load_workbook(tmp_path / "reports/activation_radar.xlsx", read_only=True)
    assert wb.sheetnames[:4] == ["Readme", "Market_Peaks", "Seasonality", "Localization"]
    peaks = pd.read_excel(tmp_path / "reports/activation_radar.xlsx", sheet_name="Market_Peaks")
    assert (peaks.loc[peaks["market"] == "US", "peak_month"] == "Nov").all()


def test_google_down_exits_75_and_writes_nothing(tmp_path):
    code = radar_cli.main(["--only", "US", "--root", str(tmp_path)], client=DownClient())
    assert code == radar_cli.EX_TEMPFAIL
    assert not any(tmp_path.iterdir())


def test_quick_single_market_mode(tmp_path):
    code = radar_cli.main(["--geo", "mx", "--keywords", "tenis,muebles", "--root", str(tmp_path)],
                          client=FakeClient())
    assert code == 0
    signals = pd.read_csv(tmp_path / "data/trends_signals.csv")
    assert set(signals["market"]) == {"MX"} and set(signals["concept"]) == {"tenis", "muebles"}
