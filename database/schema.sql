-- ============================================================
-- CRYPTO TRADING BOT — DATABASE SCHEMA
-- Platform: Supabase (PostgreSQL)
-- Generated: 24 Mar 2026
-- ============================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- CORE TRADING TABLES (4 tabel)
-- ============================================================

-- 1. trades — semua order yang pernah dieksekusi bot
CREATE TABLE trades (
    id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    pair            varchar(20)     NOT NULL,
    side            varchar(4)      NOT NULL CHECK (side IN ('buy','sell')),
    amount_usd      numeric(12,4),
    entry_price     numeric(18,8),
    exit_price      numeric(18,8),
    pnl_usd         numeric(12,4),
    fee_usd         numeric(10,4)   DEFAULT 0,
    status          varchar(10)     NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','closed','cancelled')),
    trigger_source  varchar(20),    -- rule_based / haiku / sonnet / news
    bybit_order_id  varchar(50),
    is_paper        boolean         NOT NULL DEFAULT false,
    opened_at       timestamptz     NOT NULL DEFAULT now(),
    closed_at       timestamptz
);
CREATE INDEX idx_trades_pair       ON trades(pair);
CREATE INDEX idx_trades_status     ON trades(status);
CREATE INDEX idx_trades_opened_at  ON trades(opened_at DESC);
CREATE INDEX idx_trades_is_paper   ON trades(is_paper);

-- 2. portfolio_state — snapshot modal harian
CREATE TABLE portfolio_state (
    id               uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    snapshot_date    date            UNIQUE NOT NULL,
    total_capital    numeric(12,4)   NOT NULL,
    trading_capital  numeric(12,4),
    infra_reserve    numeric(10,4)   DEFAULT 0,
    emergency_buffer numeric(10,4)   DEFAULT 0,
    current_tier     varchar(10)     NOT NULL DEFAULT 'seed'
                         CHECK (current_tier IN ('seed','growth','pro','elite')),
    active_pairs     jsonb           DEFAULT '[]',
    daily_pnl        numeric(12,4)   DEFAULT 0,
    drawdown_pct     numeric(6,4)    DEFAULT 0,
    created_at       timestamptz     NOT NULL DEFAULT now()
);
CREATE INDEX idx_portfolio_date ON portfolio_state(snapshot_date DESC);

-- 3. strategy_params — parameter strategi aktif per pair
CREATE TABLE strategy_params (
    id                      uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    pair                    varchar(20)     NOT NULL DEFAULT 'global',
    rsi_period              integer         DEFAULT 14,
    rsi_oversold            numeric(5,2)    DEFAULT 32,
    rsi_overbought          numeric(5,2)    DEFAULT 71,
    macd_fast               integer         DEFAULT 12,
    macd_slow               integer         DEFAULT 26,
    macd_signal             integer         DEFAULT 9,
    stop_loss_pct           numeric(5,2)    DEFAULT 2.2,
    take_profit_pct         numeric(5,2)    DEFAULT 4.5,
    atr_no_trade_threshold  numeric(5,2)    DEFAULT 0.8,
    position_multiplier     numeric(4,2)    DEFAULT 1.0,
    updated_by              varchar(10)     DEFAULT 'manual',
    updated_at              timestamptz     NOT NULL DEFAULT now(),
    UNIQUE(pair)
);

-- 4. pair_config — konfigurasi dan status setiap pair
CREATE TABLE pair_config (
    pair                 varchar(20)     PRIMARY KEY,
    active               boolean         NOT NULL DEFAULT false,
    strategy             varchar(30)     DEFAULT 'rsi_momentum',
    category             varchar(20),    -- Layer1 / DeFi / L2 / Oracle
    max_allocation_pct   numeric(5,2)    DEFAULT 100,
    min_capital_required numeric(10,2)   DEFAULT 50,
    lrhr_score           numeric(4,3)    DEFAULT 0,
    win_rate_30d         numeric(5,4)    DEFAULT 0,
    inactive_reason      text,
    review_date          date,
    created_at           timestamptz     NOT NULL DEFAULT now(),
    updated_at           timestamptz     NOT NULL DEFAULT now()
);

-- ============================================================
-- AI & MEMORY TABLES (4 tabel)
-- ============================================================

-- 5. opus_memory — hasil evaluasi dan pembelajaran Opus mingguan
CREATE TABLE opus_memory (
    id               uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    week_start       date            NOT NULL,
    week_end         date            NOT NULL,
    win_rate         numeric(5,4),
    total_pnl        numeric(12,4),
    max_drawdown     numeric(6,4),
    total_trades     integer         DEFAULT 0,
    sharpe_ratio     numeric(6,4),
    patterns_found   jsonb           DEFAULT '[]',
    actions_required jsonb           DEFAULT '[]',  -- [{priority, title, steps}]
    params_updated   jsonb           DEFAULT '{}',  -- {param: {old, new}}
    raw_analysis     text,
    token_cost       numeric(8,4)    DEFAULT 0,
    created_at       timestamptz     NOT NULL DEFAULT now(),
    UNIQUE(week_start)
);
CREATE INDEX idx_opus_week ON opus_memory(week_start DESC);

-- 6. news_items — semua berita yang masuk ke pipeline
CREATE TABLE news_items (
    id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    headline            text            NOT NULL,
    source              varchar(50),
    url                 text,
    pairs_mentioned     text[]          DEFAULT '{}',
    haiku_relevance     numeric(4,3),   -- 0.0–1.0
    haiku_sentiment     numeric(4,3),   -- -1.0 sampai 1.0
    haiku_urgency       numeric(4,3),   -- 0.0–1.0
    sonnet_impact       varchar(10),    -- high / medium / low
    sonnet_action       varchar(20),    -- hold / reduce / close / opportunity
    sonnet_confidence   numeric(4,3),
    price_at_news       jsonb           DEFAULT '{}',
    price_1h_after      jsonb           DEFAULT '{}',
    price_24h_after     jsonb           DEFAULT '{}',
    prediction_correct  boolean,
    injection_detected  boolean         DEFAULT false,
    published_at        timestamptz     NOT NULL,
    processed_at        timestamptz     NOT NULL DEFAULT now()
);
CREATE INDEX idx_news_pairs       ON news_items USING gin(pairs_mentioned);
CREATE INDEX idx_news_published   ON news_items(published_at DESC);
CREATE INDEX idx_news_relevance   ON news_items(haiku_relevance DESC);

-- 7. news_weights — bobot akurasi per kategori berita
CREATE TABLE news_weights (
    category        varchar(30)     PRIMARY KEY,
    weight          numeric(4,3)    NOT NULL DEFAULT 0.5,
    accuracy_1h     numeric(5,4)    DEFAULT 0,
    accuracy_24h    numeric(5,4)    DEFAULT 0,
    sample_size     integer         DEFAULT 0,
    description     text,
    last_updated    timestamptz     NOT NULL DEFAULT now()
);

-- Seed data bobot awal
INSERT INTO news_weights (category, weight, description) VALUES
    ('regulatory',   0.85, 'Berita regulasi dari pemerintah/SEC'),
    ('adoption',     0.75, 'Adopsi institusi, ETF approval'),
    ('hack_exploit', 0.90, 'Security breach, hack besar'),
    ('partnership',  0.60, 'Kerjasama project/perusahaan'),
    ('upgrade',      0.70, 'Network upgrade, hard fork'),
    ('influencer',   0.20, 'Tweet tokoh terkenal — sering noise'),
    ('macro',        0.65, 'Kondisi ekonomi makro, Fed decision'),
    ('whale',        0.75, 'Pergerakan whale on-chain');

-- 8. claude_usage — tracking penggunaan token per model
CREATE TABLE claude_usage (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    usage_date    date            NOT NULL,
    model         varchar(30)     NOT NULL, -- haiku / sonnet / opus
    calls_count   integer         DEFAULT 0,
    input_tokens  bigint          DEFAULT 0,
    output_tokens bigint          DEFAULT 0,
    cost_usd      numeric(8,4)    DEFAULT 0,
    purpose       varchar(30),    -- signal_validation / news / evaluation
    UNIQUE(usage_date, model, purpose)
);
CREATE INDEX idx_claude_date  ON claude_usage(usage_date DESC);
CREATE INDEX idx_claude_model ON claude_usage(model);

-- ============================================================
-- SYSTEM & LOG TABLES (4 tabel)
-- ============================================================

-- 9. bot_events — log semua kejadian penting
CREATE TABLE bot_events (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_type  varchar(30)     NOT NULL,
    severity    varchar(10)     NOT NULL DEFAULT 'info'
                    CHECK (severity IN ('info','warning','critical')),
    message     text,
    data        jsonb           DEFAULT '{}',
    notif_sent  boolean         DEFAULT false,
    created_at  timestamptz     NOT NULL DEFAULT now()
);
CREATE INDEX idx_events_type       ON bot_events(event_type);
CREATE INDEX idx_events_created    ON bot_events(created_at DESC);
CREATE INDEX idx_events_severity   ON bot_events(severity);

-- 10. infra_fund — tracking dana cadangan infra
CREATE TABLE infra_fund (
    id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    txn_date        date            NOT NULL,
    type            varchar(10)     NOT NULL CHECK (type IN ('credit','debit')),
    amount          numeric(10,4)   NOT NULL,
    description     varchar(100),
    balance_after   numeric(10,4)   NOT NULL,
    created_at      timestamptz     NOT NULL DEFAULT now()
);
CREATE INDEX idx_infra_date ON infra_fund(txn_date DESC);

-- 11. tier_history — riwayat naik/turun tier
CREATE TABLE tier_history (
    id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    from_tier           varchar(10),
    to_tier             varchar(10)     NOT NULL,
    capital_at_change   numeric(12,4)   NOT NULL,
    days_in_prev_tier   integer         DEFAULT 0,
    changed_at          timestamptz     NOT NULL DEFAULT now()
);

-- 12. backtest_results — hasil backtest per strategi
CREATE TABLE backtest_results (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    pair          varchar(20)     NOT NULL,
    strategy      varchar(30)     NOT NULL,
    period_start  date            NOT NULL,
    period_end    date            NOT NULL,
    total_return  numeric(8,4),
    sharpe_ratio  numeric(6,4),
    win_rate      numeric(5,4),
    max_drawdown  numeric(6,4),
    total_trades  integer         DEFAULT 0,
    params_used   jsonb           DEFAULT '{}',
    run_at        timestamptz     NOT NULL DEFAULT now()
);
CREATE INDEX idx_backtest_pair ON backtest_results(pair);
CREATE INDEX idx_backtest_run  ON backtest_results(run_at DESC);

-- ============================================================
-- ROW LEVEL SECURITY (Supabase)
-- ============================================================

ALTER TABLE trades           ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_state  ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_params  ENABLE ROW LEVEL SECURITY;
ALTER TABLE pair_config      ENABLE ROW LEVEL SECURITY;
ALTER TABLE opus_memory      ENABLE ROW LEVEL SECURITY;
ALTER TABLE news_items       ENABLE ROW LEVEL SECURITY;
ALTER TABLE news_weights     ENABLE ROW LEVEL SECURITY;
ALTER TABLE claude_usage     ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_events       ENABLE ROW LEVEL SECURITY;
ALTER TABLE infra_fund       ENABLE ROW LEVEL SECURITY;
ALTER TABLE tier_history     ENABLE ROW LEVEL SECURITY;
ALTER TABLE backtest_results ENABLE ROW LEVEL SECURITY;

-- Hanya service role yang bisa akses (backend saja)
-- Frontend tidak pernah akses database langsung

-- ============================================================
-- SEED DATA AWAL
-- ============================================================

-- Pair konfigurasi awal
INSERT INTO pair_config (pair, active, strategy, category, max_allocation_pct, min_capital_required) VALUES
    ('BTC/USDT', true,  'rsi_momentum', 'Layer1', 100,  50),
    ('ETH/USDT', false, 'rsi_momentum', 'Layer1', 40,   300),
    ('SOL/USDT', false, 'rsi_momentum', 'Layer1', 20,   500),
    ('BNB/USDT', false, 'rsi_momentum', 'Layer1', 15,   700),
    ('AVAX/USDT',false, 'rsi_momentum', 'Layer1', 10,   900),
    ('UNI/USDT', false, 'news_driven',  'DeFi',   8,    1200),
    ('LINK/USDT',false, 'rsi_momentum', 'Oracle', 7,    1500),
    ('ARB/USDT', false, 'rsi_momentum', 'L2',     5,    1800);

-- Parameter strategi default
INSERT INTO strategy_params (pair) VALUES ('global');

-- ============================================================
-- USEFUL VIEWS
-- ============================================================

-- View: performa harian ringkas
CREATE VIEW daily_performance AS
SELECT
    DATE(opened_at)         AS trade_date,
    COUNT(*)                AS total_trades,
    COUNT(*) FILTER (WHERE pnl_usd > 0) AS winning_trades,
    ROUND(AVG(CASE WHEN status='closed' THEN
        CASE WHEN pnl_usd > 0 THEN 1.0 ELSE 0.0 END
    END)::numeric, 4)       AS win_rate,
    ROUND(SUM(pnl_usd)::numeric, 4)     AS total_pnl,
    ROUND(SUM(fee_usd)::numeric, 4)     AS total_fees,
    is_paper
FROM trades
WHERE status = 'closed'
GROUP BY DATE(opened_at), is_paper
ORDER BY trade_date DESC;

-- View: saldo infra fund terkini
CREATE VIEW infra_fund_balance AS
SELECT balance_after AS current_balance, txn_date AS last_updated
FROM infra_fund
ORDER BY created_at DESC
LIMIT 1;

-- View: penggunaan Claude bulan ini
CREATE VIEW claude_monthly_cost AS
SELECT
    model,
    SUM(calls_count)   AS total_calls,
    SUM(input_tokens)  AS total_input_tokens,
    SUM(output_tokens) AS total_output_tokens,
    ROUND(SUM(cost_usd)::numeric, 4) AS total_cost_usd
FROM claude_usage
WHERE usage_date >= DATE_TRUNC('month', CURRENT_DATE)
GROUP BY model
ORDER BY total_cost_usd DESC;
