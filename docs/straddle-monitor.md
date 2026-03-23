# SPX Straddle Monitor

Public route:

- `https://spx0.com/straddle`

Backend endpoint:

- `GET /api/straddle-monitor`

Required server env:

```env
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
```

Supabase setup:

1. Open the SQL editor in your Supabase project.
2. Run [`supabase/straddle_monitor_intraday.sql`](/Users/cparikh/ws/personal/public/claw-skill-public-dot-com/supabase/straddle_monitor_intraday.sql).
3. Run [`supabase/straddle_monitor_daily_close.sql`](/Users/cparikh/ws/personal/public/claw-skill-public-dot-com/supabase/straddle_monitor_daily_close.sql).
4. Deploy the app.

Hetzner close-capture scheduler:

1. Copy [`deploy/systemd/spx0-straddle-close.service`](/Users/cparikh/ws/personal/public/claw-skill-public-dot-com/deploy/systemd/spx0-straddle-close.service) to `/etc/systemd/system/`.
2. Copy [`deploy/systemd/spx0-straddle-close.timer`](/Users/cparikh/ws/personal/public/claw-skill-public-dot-com/deploy/systemd/spx0-straddle-close.timer) to `/etc/systemd/system/`.
3. Run `sudo systemctl daemon-reload`.
4. Run `sudo systemctl enable --now spx0-straddle-close.timer`.
5. Verify with `systemctl status spx0-straddle-close.timer --no-pager`.

Manual capture:

```bash
./run capture_straddle_close --force
```

Notes:

- The monitor is SPX-only in v1.
- Option data and history are both recorded at 60-second cadence.
- History writes happen only during regular market hours.
- The scheduler runs every 5 minutes and the script self-gates to the 4:00 PM ET close window.
- If the Supabase tables are missing, the page still loads but the persisted history sections stay empty.
