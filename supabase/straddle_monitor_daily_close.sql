create extension if not exists pgcrypto;

create table if not exists public.straddle_monitor_daily_close (
  id uuid primary key default gen_random_uuid(),
  symbol text not null,
  session_date date not null,
  captured_at timestamptz not null,
  expiration date not null,
  days_to_expiry integer not null,
  strike numeric not null,
  spot numeric not null,
  straddle_mid numeric not null,
  implied_move_pct numeric,
  put_call_skew numeric,
  iv numeric,
  recorded_at timestamptz not null default now()
);

create unique index if not exists straddle_monitor_daily_close_symbol_session_exp_idx
  on public.straddle_monitor_daily_close (symbol, session_date, expiration);
