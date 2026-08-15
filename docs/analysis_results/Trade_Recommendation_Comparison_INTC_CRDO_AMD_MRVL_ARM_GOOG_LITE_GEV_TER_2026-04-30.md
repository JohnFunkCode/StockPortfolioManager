# Trade Recommendation Comparison: INTC, CRDO, AMD, MRVL, ARM, GOOG, LITE, GEV, TER

Generated: 2026-04-30

Source:
- Live `get_trade_recommendation(symbol, capital=5000)` MCP tool calls run on 2026-04-30
- Live `get_earnings_calendar(symbol)` MCP tool calls run on 2026-04-30

## Summary Table

| Symbol | Trade Type | Action | Confidence | Bull | Bear | Net | Entry | Target | Stop Loss | Risk/Reward | Earnings Date | Earnings Risk |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `INTC` | `SKIP` | HOLD | LOW | 9 | 10 | -1 | 94.48 | n/a | n/a | n/a | 2026-07-23 | LOW |
| `CRDO` | `BULL_CALL_SPREAD` | BUY | HIGH | 9 | 1 | 8 | 174.01 | 218.30 | 150.04 | 1.85 | 2026-06-01 | LOW |
| `AMD` | `WEAK_LONG` | BUY | LOW | 9 | 7 | 2 | 354.49 | 357.05 | 274.32 | 0.03 | 2026-05-05 | CRITICAL |
| `MRVL` | `LONG_CALL` | BUY | HIGH | 11 | 4 | 7 | 165.15 | 176.29 | 158.72 | 1.73 | 2026-05-28 | MODERATE |
| `ARM` | `SKIP` | HOLD | HIGH | 8 | 2 | 6 | 210.32 | n/a | n/a | n/a | 2026-05-06 | CRITICAL |
| `GOOG` | `WEAK_LONG` | BUY | LOW | 10 | 8 | 2 | 381.94 | 361.65 | 321.50 | 0.34 | 2026-07-23 | LOW |
| `LITE` | `BULL_CALL_SPREAD` | BUY | HIGH | 8 | 1 | 7 | 902.32 | 936.03 | 892.80 | 3.54 | 2026-05-05 | CRITICAL |
| `GEV` | `LONG_CALL` | BUY | HIGH | 9 | 3 | 6 | 1083.46 | 1173.21 | 1014.49 | 1.30 | 2026-07-29 | LOW |
| `TER` | `BULL_CALL_SPREAD` | BUY | HIGH | 15 | 3 | 12 | 343.47 | 427.58 | 290.78 | 1.60 | n/a | UNKNOWN |

## Options Context Table

| Symbol | Avg IV % | High IV | ATM Call Ask | ATM Put Ask | Put/Call Ratio | MM Hedge Bias | Gamma Wall | Delta Flip | Net DAOI Shares |
| --- | ---: | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| `INTC` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `CRDO` | 295.4 | yes | 3.30 | 5.10 | 1.05 | sell_on_rally | 120.0 | 87.0 | 15,168 |
| `AMD` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `MRVL` | 37.8 | no | 2.40 | 3.15 | 1.14 | sell_on_rally | 160.0 | 77.0 | 112,194 |
| `ARM` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `GOOG` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `LITE` | 259.4 | yes | 30.90 | 18.00 | 1.98 | sell_on_rally | 900.0 | 1115.0 | 8,719 |
| `GEV` | 30.8 | no | 10.80 | 17.40 | 1.82 | sell_on_rally | 900.0 | 685.0 | 8,811 |
| `TER` | 178.0 | yes | 8.50 | 5.50 | 0.81 | sell_on_rally | 350.0 | 507.5 | 947 |

## Qualitative Interpretation

### INTC

`INTC` is a high-conflict name and the tool resolved that conflict as `SKIP`. Bullish momentum, strong call activity, strong relative strength, and positive news are offset by extreme overbought readings, a widening spread, inflecting-negative revenue, and decelerating EPS acceleration. Earnings are not the issue here because the next report is on `2026-07-23` with `LOW` event risk; the issue is signal disagreement and stretched price action. The tool's warnings point to no clear edge despite strong upside participation from options traders.

### CRDO

`CRDO` is the cleanest bullish setup in the group. The tool returned `BULL_CALL_SPREAD` with `HIGH` confidence and a `net_score` of `8`, supported by bullish MACD, accelerating revenue, moderate EPS acceleration, strong relative strength, and strongly positive news sentiment. The main structural caveat is options pricing and dealer positioning: IV is extremely high at `295.4%`, put/call ratio is slightly above parity, and the hedge bias is `sell_on_rally`, so the bullish thesis is strong but upside may be less efficient than the raw score suggests. Earnings are on `2026-06-01`, which keeps event risk in the `LOW` bucket.

### AMD

`AMD` still screens as bullish, but the recommendation quality is weak. The tool returned `WEAK_LONG` with `LOW` confidence, and the setup has a near-zero `risk_reward_ratio` of `0.03`, which makes it materially less attractive than the higher-conviction names. Drivers include bullish momentum, aggressive call buying, strong options positioning, strong relative strength, and positive news, but those positives are diluted by overbought conditions plus decelerating revenue and EPS acceleration. The key blocker is timing: earnings are on `2026-05-05`, only `5` days away, and the earnings tool marked this `CRITICAL`, which matches the warning to avoid new options exposure because of imminent IV crush.

### MRVL

`MRVL` is a relatively strong tactical long with supportive timing. The tool returned `LONG_CALL` with `HIGH` confidence, driven by bullish MACD, strong call activity, narrowing bid/ask spread, heavy positive net call delta, accelerating revenue, strong relative strength, and positive news sentiment. The caveats are familiar: the stock is overbought, EPS acceleration is decelerating, and dealer positioning is still `sell_on_rally`, so momentum continuation needs price confirmation rather than blind chasing. Earnings are scheduled for `2026-05-28`, which is `28` days out and classified as `MODERATE`; that creates a pre-earnings IV expansion backdrop that the tool explicitly treated as supportive for long calls.

### ARM

`ARM` has a bullish underlying signal stack but was still downgraded to `SKIP` because of earnings timing. The tool saw bullish MACD, strong call activity, large positive net call delta, solid fundamentals, and strong relative strength, but it also flagged a volume exhaustion top and included an earnings override warning. The decisive issue is that earnings are on `2026-05-06`, only `6` days away, which is `CRITICAL` event risk. The net takeaway is that the stock may still be institutionally supported, but the tool would rather stand aside than take fresh options risk into the report.

### GOOG

`GOOG` is another case where the direction is nominally bullish but the setup quality is not compelling. The tool returned `WEAK_LONG` with `LOW` confidence even though fundamentals are the best in the set (`strong_compounder`), revenue is accelerating, relative strength is strong, options positioning is bullish, and news is modestly positive. The problem is that price is already extended: Bollinger position, RSI, and Stochastic all read overbought, while EPS acceleration is decelerating and the hedge bias remains `sell_on_rally`. Earnings are not an immediate risk because the next report is on `2026-07-23`, but the tool still frames this as a low-quality entry rather than a clean long.

### LITE

`LITE` scored well enough for `BULL_CALL_SPREAD`, but the event-risk warning is the first thing that matters. The bullish side comes from strong call activity, accelerating revenue, moderate EPS acceleration, and extraordinary long-term relative strength; news sentiment is neutral rather than a tailwind. Against that, the drivers include bearish MACD and the warnings explicitly flag both `CRITICAL` earnings risk and a `sell_on_rally` dealer structure. With earnings on `2026-05-05`, only `5` days away, the report should treat the raw bullish recommendation as materially constrained by imminent event risk and very high IV (`259.4%`).

### GEV

`GEV` is a higher-conviction bullish name with cleaner calendar risk than most of the list. The tool returned `LONG_CALL` with `HIGH` confidence, supported by bullish MACD, a moderately bullish dragonfly doji, strong call buying, strong relative strength, and strongly positive news sentiment. The weaker points are widening spread, inflecting-negative revenue, decelerating EPS acceleration, and a `sell_on_rally` dealer bias, so this is not a pristine fundamental trend story even though the tactical score is solid. Earnings are on `2026-07-29`, which puts the name in the `LOW` event-risk category and leaves room for the setup to play out without a near-term catalyst deadline.

### TER

`TER` produced the highest net score in the group and looks like the strongest raw signal composite. The tool returned `BULL_CALL_SPREAD` with `HIGH` confidence and a `net_score` of `12`, driven by oversold Stochastic readings, volume-bottom behavior, OBV bullish divergence, strong call activity, strong fundamentals, accelerating revenue, strong EPS acceleration, strong relative strength, and positive news sentiment. The main caution is structural rather than directional: MACD has a bearish crossover, options IV is high at `178.0%`, and dealer positioning is again `sell_on_rally`, so the system preferred a spread rather than outright calls. Earnings timing is less clear because the earnings calendar came back `UNKNOWN`, so this is the strongest technical-fundamental blend in the batch but with incomplete event-date visibility.

## Cross-Symbol Takeaways

- Strongest clean bullish setup: `CRDO`
- Strongest raw composite score: `TER`
- Best pre-earnings momentum setup: `MRVL`
- Bullish setups materially impaired by immediate earnings risk: `AMD`, `ARM`, `LITE`
- Low-conviction or conflicted holds: `INTC`, `GOOG`
- Strong bullish setup with low near-term event risk: `GEV`
