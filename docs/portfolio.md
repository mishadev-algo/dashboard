# Portfolio analytics

Open `/portfolio` for analysis across accounts, or `/accounts` for an account's separate realized-return chart. Portfolio dates use UTC so cross-account daily comparisons share the same boundary. Account pages keep the configured account day zone; following a link preserves calendar dates, but a non-UTC account can have different boundary deals.

## Strategy pages and connected reports

Click a strategy in an account's strategy table, recent deals, or current positions to open `/strategy?server=…&login=…&strategy=…`. The page is always scoped to that exact broker, account and configured strategy name. Portfolio account/strategy breakdowns have **Strategy details** links. Standalone commissions and income/charges are not linked as if they were strategies.

The strategy page includes a return curve, optional drawdown and net-PnL curves, exit statistics, current positions and initially the latest 50 filtered deals, plus:

- Monthly PnL tiles: click a month to narrow the whole page to that month.
- A daily PnL calendar for the latest active month in the selected range: click a day to filter and jump to its deals.
- Day-of-week PnL, active-day counts and average active-day PnL, plus linked best/worst active days. Entry costs count on their booking day; these are realized-result views, not signals about future performance.
- Symbol links that filter the strategy's curve, metrics, calendar and deals. The instrument comparison table deliberately retains all instruments of that strategy for navigation. Current positions follow the symbol filter but not historical dates.
- 1M / 3M / 6M / full-history shortcuts, return-basis controls, a parent-account link, matching strategy/account pages, and a same-currency comparison in Portfolio. Portfolio links include all strategy symbols and use UTC dates.

Account-origin links retain the configured account day zone; portfolio-origin links pass `zone=UTC`. The page labels its day zone. Date, symbol and return-basis controls survive month/day navigation. A strategy with only open positions can show those positions, but does not get an invented historical return. Account-capital and fixed-capital return definitions are the same as below. All-strategy account/portfolio calendars retain attributable income/charges; individual strategy calendars include only that strategy's trade deals and their recorded costs.

**Show more** progressively expands closed deals on Accounts by 15 rows and deals on a strategy/EA detail page by 50 rows. It preserves account, strategy, dates, symbol, day zone and return basis, and returns to the deals section after loading. Counts show how many matching rows are displayed; the button disappears when all are shown. Changing the account or applying new filters starts a fresh list. The `/eas` attachment-status page has no trade history; deal pagination is on the strategy detail page.

Charts use a shared SVG style with gradient area, date/grid labels, a final-value marker and keyboard-focusable point tooltips. The path preserves every recorded point, including intraday extremes; hover targets show the latest recorded value per day and are thinned to about 120 on long histories. Mobile charts scroll inside their own container. Chart improvements do not smooth, interpolate missing returns, or change calculation formulas.

The report design draws on [Myfxbook's monthly performance and system comparisons](https://www.myfxbook.com/features), [Tradervue's calendar and overview reports](https://www.tradervue.com/help/reports/reports_overview), and [TradeZella's performance calendar and strategy analysis](https://www.tradezella.com/trading-journal). These are UI references; no account data is sent to them. Trade replay, MAE/MFE and intratrade equity metrics were not added because this ledger does not contain the price paths they require.

## Select accounts and strategy combinations

By default, the portfolio includes all accounts in the current currency and all their strategies. Use the account checkboxes to select several accounts. Under **Strategies by account**, use **Clear**, then check the exact strategy/account pairs to compare, for example C15 on account A and C19 on account B. Changing a checkbox selects the corresponding checked-only mode automatically; the Include dropdown also works without JavaScript. **Select all** restores all mode. Click **Build portfolio** to apply the selection. A pair on an excluded account stays excluded; an empty or unknown selection never silently falls back to all accounts.

Individual-strategy comparisons use the intersection of their recorded spans:

- Start: the latest first recorded trade day among selected strategy/account pairs, including entry deals. C15 first recorded in July and C19 first recorded in August produce an August start. This is an observed-history boundary, not a known EA deployment date.
- End: the earliest last complete account snapshot day among participating accounts, in UTC. The last trade does not end a strategy's history; quiet days count as zero realized PnL.
- Optional From/To dates narrow this intersection. They cannot extend it. Leave them blank for automatic boundaries; **Full shared history** removes dates while preserving selection, return basis and capital.
- With all-strategies mode, the intersection uses recorded account spans instead. An individually selected strategy with open positions but no recorded trades has unknown history and cannot produce a common-period comparison. Disjoint spans or dates outside the intersection produce an explicit empty state, not an extrapolated curve. Internal gaps in collection cannot be proven absent from this ledger.

All selected PnL charts, strategy metrics, relationships, current floating PnL and open-position breakdowns use the same account/pair selection. Current positions still ignore historical date filters. Latest balances and transfers are whole-account context for the participating accounts, each counted once. Account links open the full account view. Account-level commissions, dividends and other unallocated income/charges are included in all-strategies mode; individual-strategy selection excludes them because there is no reliable attribution. Costs already recorded on selected entry/exit deals remain included.

The combined percentage chart offers two explicit bases:

1. **Participating account capital** (default): at each timestamp, divide selected net PnL by the sum of reconstructed actual balances of participating accounts and geometrically link the changes. An account shared by two selected strategies is counted once. Other strategy results change subsequent actual account capital, but never selected PnL. Deposits/withdrawals change the capital base without creating returns. This is a selected-result index relative to account capital, not a separately allocated strategy ROI. Per-account comparison rows use this basis independently of the combined chart's basis.
2. **Fixed starting capital**: enter a positive capital in the selected currency. The modeled return is `100 * cumulative selected net PnL / starting capital`. Deposits/withdrawals and other strategies are completely ignored. Drawdown uses the modeled value `starting capital + cumulative selected PnL`. This replays recorded trade results and sizing on a chosen base; it does not model rescaled lots or reinvestment. Missing actual account balances do not prevent this calculation. Invalid/missing capital suppresses percentages but keeps monetary PnL visible.

Currency changes via the selector reset account/strategy scope to all in that currency. No FX conversion is inferred. Filters live in the URL and survive form submission; **Reset selection** returns to defaults.

## Realized return without deposits and withdrawals

The primary chart on Accounts, strategy detail and Portfolio shows cumulative net realized **money profit** in the account currency. Percentage return and its drawdown are in a separate expandable section. A withdrawal does not move the money-profit curve. The percentage index uses changing capital and can be negative even when lifetime money profit is positive: deposit 1,000, earn 200, withdraw 1,100, then lose 40 gives **+160 money profit** but **-28% linked return**. Accounts also offers a profit breakdown of trade profit, trade costs, separate commissions and account income/charges for comparison with recorded history.

There are no stored historical equity valuations. The return chart therefore measures **realized balance performance**, including recorded commissions, swap and fees, but excluding floating PnL. It is not an equity TWR or an account-lifetime result unless the stored ledger is complete.

Starting capital is reconstructed from the latest verified balance minus all recorded balance-changing deals. MT5 credit (type 3) is excluded because it is separate from balance. Recorded charges, interest, dividends, franked dividends and tax (types 4, 12, 15, 16, 17) count toward the result as `unallocated income / charges`; see [MetaQuotes deal types](https://www.mql5.com/en/docs/constants/tradingconstants/dealproperties). These classifications are applied when reading the ledger, without rewriting old records or guessing strategy identity from comments. Deals outside the selected dates are still used to reconstruct capital. The selected return index starts at 1 (displayed as 0%). For each timestamp with a trading result `P` and positive preceding balance `B`:

```text
index = index * (B + P) / B
return_percent = (index - 1) * 100
```

Deposits and withdrawals update `B` but leave `index` unchanged. For example, depositing 1,000, earning 100, adding 1,100, then earning 220 gives `(1.10 * 1.10 - 1) * 100 = 21%`, not 142%. Withdrawing money also leaves the index unchanged. Maximum return drawdown uses the largest percentage fall from a previous index peak, including the starting index.

Geometric linking and separation of external cash flows follow the general approach described in the [GIPS calculation methodology](https://www.gipsstandards.org/standards/gips-standards-for-firms/gips-standards-handbook-for-firms/). This implementation uses balance, not portfolio fair-value valuations, and does not claim GIPS compliance.

Percentage returns are unavailable when balance is missing, trading capital is non-positive, a result would push capital below zero, an unclassified balance adjustment occurs, or a transfer and nonzero trading result share an indistinguishable timestamp. The UI explains the reason and retains the cash-flow-free monetary PnL series. Missing historical deals cannot be detected reliably from a single balance snapshot; the interface shows recorded coverage and snapshot freshness.

## Strategy comparison

- Strategies are grouped by explicitly configured strategy name, with account identity kept as `(server, login)`. Keep names consistent only for strategies intended to be compared. `unmapped` activity and standalone `unallocated commission` remain visible.
- Net PnL includes entries, exits, commission, swap and fees. Cash movements never enter strategy PnL. Each currency is analyzed separately, without implicit FX conversion.
- Exit win rate, profit factor and average exit use the net amounts on exit deals, including partial closes and reversal exits. These are **exit-deal statistics**, not complete round-trip trade statistics. Entry fees are in net PnL but are not reassigned to exits. A zero exit counts in the denominator of win rate; profit factor is infinite with wins and no losses, and unavailable without either.
- PnL drawdown is the maximum peak-to-trough decline of cumulative realized strategy PnL, starting at zero for the selected period. It is a money amount. No strategy percentage return is shown because allocated capital per strategy is not recorded.
- Absolute contribution is `abs(strategy PnL) / sum(abs(strategy PnL))`. This remains interpretable when total PnL is near zero; it is neither a capital allocation nor a risk weight.
- Current floating PnL is position profit plus swap. Current positions and balances ignore historical date filters and show snapshot freshness. They are not period-end positions or synchronized equity measurements.
- Shared-symbol rows link to strategy/account comparisons and show buy and sell lots separately. Contract sizes can differ between brokers; lots are not summed across symbols or presented as monetary risk.
- Daily PnL relationships use Pearson correlation in UTC for up to 12 mapped strategies ranked by absolute net PnL. Each needs at least 20 recorded active days; pairs need 20 days within overlapping spans where either strategy has a deal. A missing deal on an included day contributes zero; constant series have no correlation. This is a conditional active-day PnL statistic, not return correlation or proof of diversification. Sizing and incomplete histories affect it.

The feature reads the existing schema and does not change collection, MT5 terminals, trading settings, or historical records. Restart only the dashboard server to load the new views; a collector update is not required.
