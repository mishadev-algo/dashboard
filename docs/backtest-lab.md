# Backtest Lab

Open `/backtests` to import saved strategy tests and explore fixed-allocation portfolios. This page uses separate `backtest_runs` and `backtest_days` tables. It does not read live MT5 terminals, collector state, or broker deals.

## Import

Import one strategy run per file (up to 4 MB). Enter its strategy name, three-letter account currency, and positive starting capital. The original file hash prevents duplicate imports. Accepted files:

- CSV with `date,net_pnl` for daily net results.
- CSV with `time,profit,commission,swap,fee` for deal results. Costs are added to profit. An optional `type` column skips `balance`, `credit`, `deposit`, `withdrawal`, and `transfer` rows.
- XLSX with the same dated result columns as CSV. The importer reads worksheet values, including Excel date cells, and chooses the sheet with the most dated result rows. Exported MT5 Deals tables are supported when they contain `Time` and `Profit` columns.
- MT5 HTML report with a dated Deals table containing `Time` and `Profit`; optional `Commission`, `Swap`, and `Fee` columns are included. A summary-only report cannot be imported because it lacks a dated series. The standard Deals layout is described in [MetaQuotes' report parser article](https://www.mql5.com/en/articles/5436).

Dates use `YYYY-MM-DD` or `YYYY.MM.DD`, with optional time. All imported dates are treated as the report's calendar days; no timezone conversion is attempted. The dashboard cannot infer missing trades, initial deposits, open equity, broker currency conversion, or fees absent from the file. Review a few daily totals against the source report before comparing runs.

## Portfolio analysis

Select up to ten runs in one currency and set positive relative allocation weights (equal by default). The selected chart and ranking use their common date span, so every candidate is measured on the same period. A run's first and last dated rows define its observed span; zero PnL is assumed between those dates. The page shows individual run results, combined return and drawdown, pairwise daily Pearson correlations, and the top 20 equal-weight combinations ranked by return divided by maximum drawdown. Click a combination to inspect its full chart, then adjust its weights.

Each run's daily return is its net PnL divided by the starting capital entered at import. The selected portfolio sums those daily returns using normalized allocation weights and accumulates them without compounding or resizing original trades. Maximum drawdown measures the decline from the highest model portfolio value, starting at 100%. Correlations use days in the shared span where either run had nonzero PnL; at least 20 active days and varying results are required. A low correlation is historical evidence, not a forward diversification guarantee. Rankings can overfit the tested period, especially with many candidates.

The combined chart shows cumulative PnL in the selected currency; the percentage chart remains available below it. Absolute return and drawdown in the summary cards use the sum of the selected runs' starting capitals as the model portfolio capital. The drawdown amount is the peak-to-trough loss at the largest percentage drawdown, so it is not generally the displayed drawdown percentage multiplied by starting capital. Changing allocation weights reallocates this same model capital between runs.

The normal dashboard proxy authentication covers this page. Import POST requests also require the form token from a page load. Restart the dashboard server to expose the new route; the collector and MT5 terminals require no change.
