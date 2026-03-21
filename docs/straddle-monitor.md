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
3. Deploy the app.

Notes:

- The monitor is SPX-only in v1.
- Option data and history are both recorded at 60-second cadence.
- History writes happen only during regular market hours.
- If the table is missing, the page still loads but the two intraday charts stay empty.
