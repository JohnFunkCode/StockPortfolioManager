# QuantCore Database ERD

```mermaid
erDiagram
    SYMBOLS ||--o{ POSITIONS : references
    SYMBOLS ||--o{ PLAN_INSTANCES : references
    SYMBOLS ||--o{ ALERTS : references
    PLAN_TEMPLATES ||--o{ PLAN_INSTANCES : defines
    PLAN_INSTANCES ||--o{ PLAN_RUNGS : contains
    PLAN_INSTANCES ||--o{ PLAN_INSTANCES : supersedes
    POSITIONS ||--o{ PLAN_INSTANCES : linkedTo
    PLAN_RUNGS ||--o{ ALERTS : triggers
    OPTIONS_SNAPSHOTS ||--o{ OPTIONS_EXPIRATIONS : contains
    OPTIONS_EXPIRATIONS ||--o{ OPTIONS_CONTRACTS : contains

    SYMBOLS {
        int symbol_id PK
        string ticker UK
        string name
        string exchange
        string currency
        text created_at
    }

    OHLCV {
        string symbol PK
        string interval PK "DEFAULT '1d'"
        int ts PK
        real open
        real high
        real low
        real close "NOT NULL, CHECK > 0"
        real adj_close
        real volume
        string status "DEFAULT 'CLOSED'"
        string data_vendor "DEFAULT 'yfinance'"
        int ingested_at
    }

    FETCH_LOG {
        string symbol PK
        string interval PK
        int fetched_at
    }

    POSITIONS {
        int position_id PK
        int symbol_id FK
        text opened_at
        real entry_price
        int shares
        real cost_basis_total
        string account
        text notes
    }

    PLAN_TEMPLATES {
        int template_id PK
        string name
        int is_dynamic_h
        int history_window_days
        int n_iterations
        real alpha
        real min_h
        real max_h
        real fixed_h
        string drift_method "DEFAULT 'CAGR'"
        string vol_method "DEFAULT 'LOGRET_STD'"
        string stats_price_series "DEFAULT 'adj_close'"
        text created_at
        text notes
        text metadata_json
    }

    PLAN_INSTANCES {
        int instance_id PK
        int template_id FK
        int symbol_id FK
        int position_id FK "nullable"
        string status "DEFAULT 'ACTIVE'"
        text created_at
        text asof_date
        real price_asof
        int shares_initial
        real v0_floor
        real capital_at_risk
        text history_end_date
        int history_window_days
        real r_daily
        real annual_vol
        real h_threshold
        int n_iterations
        string stats_price_series
        int supersedes_instance_id FK "nullable"
        text notes
        text metadata_json
    }

    PLAN_RUNGS {
        int rung_id PK
        int instance_id FK
        int rung_index
        real target_price
        int shares_before
        int shares_sold_planned
        int shares_after_planned
        real expected_days_from_now
        text expected_date
        real gross_harvest_planned
        real cumulative_harvest_planned
        real remaining_value_planned
        real total_wealth_planned
        real total_return_planned
        string status "DEFAULT 'PENDING'"
        text triggered_at
        real trigger_price
        text executed_at
        real executed_price
        int shares_sold_actual
        real gross_harvest_actual
        real tax_paid_actual
        real net_harvest_actual
        text notes
    }

    ALERTS {
        int alert_id PK
        int rung_id FK
        int symbol_id FK
        int instance_id FK
        string alert_type "DEFAULT 'PRICE_GE'"
        real threshold_price
        string status "DEFAULT 'ACTIVE'"
        text created_at
        text last_checked_at
        text fired_at
        real fired_price
        int cooldown_seconds
        string channel
        string destination
        text message_template
    }

    OPTIONS_SNAPSHOTS {
        int snapshot_id PK
        string symbol
        text captured_at
        real price
        real bb_upper
        real bb_middle
        real bb_lower
        int bb_period "DEFAULT 20"
        string chain_type "DEFAULT 'atm'"
    }

    OPTIONS_EXPIRATIONS {
        int expiration_id PK
        int snapshot_id FK
        string expiration
        real put_call_ratio
        int total_call_oi
        int total_put_oi
        int total_call_vol
        int total_put_vol
        real avg_call_iv
        real avg_put_iv
    }

    OPTIONS_CONTRACTS {
        int contract_id PK
        int expiration_id FK
        string kind "CHECK IN ('call', 'put')"
        real strike
        real last_price
        real bid
        real ask
        real implied_vol
        int volume
        int open_interest
        int in_the_money
    }

    GAMMA_WALL_HISTORY {
        int id PK
        string symbol
        text date_only
        text captured_at
        real price
        real gamma_wall_strike
        real delta_flip_strike
        real dist_to_flip_pct
        real net_daoi_shares
        real call_daoi_shares
        real put_daoi_shares
        string mm_hedge_bias
        string signal
        text expirations_scanned
        text payload
    }

    OPTIONS_POSITIONS {
        int position_id PK
        string symbol
        string kind "CHECK IN ('call', 'put')"
        real strike
        string expiration
        int contracts "DEFAULT 1"
        real purchase_price
        text purchase_date
        real target_price
        string status "DEFAULT 'ACTIVE'"
        text closed_at
        text notes
    }

    NEWS_ARTICLES {
        int article_id PK
        string symbol
        string title
        text summary
        string publisher
        text url
        text published_at
        string source
        text fetched_at
        string sentiment
        real sentiment_score
        real positive_score
        real negative_score
        real neutral_score
    }

    SENTIMENT_SNAPSHOTS {
        int id PK
        string symbol
        text captured_at
        int article_count "DEFAULT 0"
        int positive_count "DEFAULT 0"
        int negative_count "DEFAULT 0"
        int neutral_count "DEFAULT 0"
        int scored_count "DEFAULT 0"
        string overall_sentiment
        text articles_json
    }

    FUNDAMENTALS_HISTORY {
        string symbol PK
        string data_type PK
        int fetched_at PK
        text payload
    }
```

## Key Relationships

### Portfolio & Positions
- **SYMBOLS** is the central entity for all stock data
- **POSITIONS** links equity positions to symbols
- **PLAN_INSTANCES** and **PLAN_RUNGS** form the Harvester system for systematic selling

### Options Data
- **OPTIONS_SNAPSHOTS** captures a point-in-time snapshot
- **OPTIONS_EXPIRATIONS** breaks down by expiration date
- **OPTIONS_CONTRACTS** contains individual strike data
- **GAMMA_WALL_HISTORY** tracks market-maker positioning over time
- **OPTIONS_POSITIONS** tracks owned options contracts

### News & Sentiment
- **NEWS_ARTICLES** stores individual articles by symbol
- **SENTIMENT_SNAPSHOTS** aggregates sentiment at a point in time

### Harvester System
- **PLAN_TEMPLATES** define the algorithm parameters
- **PLAN_INSTANCES** create specific instances for each symbol
- **PLAN_RUNGS** define the harvest targets (price levels where to sell)
- **ALERTS** trigger when rungs are hit, linked to Discord notifications

## Indices

All tables have appropriate indices for common queries:
- `ohlcv`: lookup by (symbol, interval, ts)
- `plan_instances`: unique constraint on one active plan per symbol
- `plan_rungs`: instance status tracking
- `alerts`: symbol and status filtering
- `options_*`: snapshot and expiration lookups
- `news_articles`: symbol and published_at for temporal queries
- `sentiment_snapshots`: symbol and captured_at for time-series
