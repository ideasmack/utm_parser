-- Suggested staging table for UTM attribution (adjust types for your POS / database)
CREATE TABLE utm_attribution (
    attribution_id        BIGINT IDENTITY PRIMARY KEY,
    pos_customer_id       VARCHAR(64)   NULL,   -- filled in once the POS record exists
    pos_order_id          VARCHAR(64)   NULL,
    location_id           VARCHAR(64)   NULL,   -- franchise / territory
    touch_type            VARCHAR(10)   NOT NULL CHECK (touch_type IN ('first', 'last')),
    utm_source            VARCHAR(255)  NULL,
    utm_medium            VARCHAR(255)  NULL,
    utm_campaign          VARCHAR(255)  NULL,
    utm_term              VARCHAR(255)  NULL,
    utm_content           VARCHAR(255)  NULL,
    utm_id                VARCHAR(255)  NULL,
    utm_source_platform   VARCHAR(255)  NULL,
    utm_creative_format   VARCHAR(255)  NULL,
    utm_marketing_tactic  VARCHAR(255)  NULL,
    click_ids_json        VARCHAR(2000) NULL,
    extra_params_json     VARCHAR(2000) NULL,
    landing_url           VARCHAR(2000) NULL,   -- parser truncates to 2000 chars
    referrer              VARCHAR(2000) NULL,
    session_id            VARCHAR(128)  NULL,
    captured_at           DATETIME2     NOT NULL,
    received_at           DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_utm_campaign ON utm_attribution (utm_source, utm_medium, utm_campaign);
CREATE INDEX ix_utm_customer ON utm_attribution (pos_customer_id);
