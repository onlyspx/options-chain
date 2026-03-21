create extension if not exists pgcrypto;

create table if not exists public.straddle_monitor_intraday (
  id uuid primary key default gen_random_uuid(),
  symbol text not null,
  expiration date not null,
  days_to_expiry integer not null,
  strike numeric not null,
  spot numeric not null,
  straddle_mid numeric not null,
  implied_move_pct numeric,
  bucket_ts timestamptz not null,
  recorded_at timestamptz not null default now()
);

create unique index if not exists straddle_monitor_intraday_symbol_expiration_bucket_idx
  on public.straddle_monitor_intraday (symbol, expiration, bucket_ts);
