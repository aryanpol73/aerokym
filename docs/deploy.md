# Deploying Aerokym

This guide has not been run end to end: nothing has been deployed yet. Treat
it as a checklist to verify step by step, not as a tested recipe.

Target: one small VPS for the database, workers and API; Vercel's free tier
for the web app. Total cost should stay under Rs 1,000 a month.

## What you need first

| Item | Why |
|---|---|
| A VPS with 2 GB RAM and 25 GB disk, Docker installed | Database image alone is about 4 GB |
| A domain or subdomain for the API, for example `api.example.org` | HTTPS for browser requests |
| OpenSky API client id and secret | Which aircraft are in cruise |
| adsb.lol API key (optional) | Raises the trace budget from 500 to 1,000 requests a day |

## 1. Backend on the VPS

```bash
git clone https://github.com/aryanpol73/aerokym && cd aerokym
```

```bash
cp .env.example .env
```

Edit `.env`: fill in the OpenSky and adsb.lol values, and set
`ADSBLOL_DAILY_REQUESTS=900` if you have a key.

Before starting, change the database password. `docker-compose.yml` ships
with `aerokym` / `aerokym`, which is only acceptable because the database port
is bound to `127.0.0.1`. Change `POSTGRES_PASSWORD` and the matching
`AEROKYM_DATABASE_URL`.

```bash
docker compose --profile live up --build -d db migrate airports api opensky adsblol detect-live weather validate
```

The `web` service is left out on purpose: Vercel serves the web app.

Check it:

```bash
docker compose logs --tail 20 opensky adsblol detect-live validate
```

```bash
curl http://localhost:8000/api/meta
```

Expect `opensky` to report aircraft counts within two minutes, `adsblol` to
report one aircraft every few minutes, and `/api/meta` to show a recent
`latest_event` within about half an hour.

## 2. HTTPS in front of the API

The API listens on `127.0.0.1:8000` only. Put a reverse proxy with automatic
certificates in front of it. With Caddy, the whole configuration is:

```
api.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

Two things change behind a proxy:

- **Rate limiting.** The API's built-in limiter counts requests per client
  address, and behind a proxy every request appears to come from the proxy.
  Limit at the proxy instead, or the limit becomes one shared bucket.
- **Caching.** Responses carry `Cache-Control: public, max-age=60`. A CDN in
  front (Cloudflare's free tier is enough) will absorb a traffic spike.

## 3. Web app on Vercel

Import the GitHub repository in Vercel and set:

| Setting | Value |
|---|---|
| Root directory | `web` |
| Environment variable `NEXT_PUBLIC_API_URL` | `https://api.example.org` |

`NEXT_PUBLIC_API_URL` is baked in at build time, so redeploy after changing
it.

## 4. Before announcing it

- [ ] The map shows cells over India less than two minutes after a detector
      run, and `/api/meta` shows a fresh `latest_event`.
- [ ] The accuracy page shows at least a few independent days. Launching on
      the one-day calibration alone invites the obvious criticism.
- [ ] Load the map on a mid-range phone on mobile data and time it. The
      target is under 3 seconds; this has never been measured.
- [ ] The disclaimer is visible on every page and in every API response.
- [ ] `docs/data-sources.md` limits are re-checked: providers change them.
- [ ] Replace the GIF placeholder in the README with a real recording.
- [ ] OpenSky's terms are research and non-commercial: confirm the project
      still fits, and that the attribution is in place.

## Running costs to watch

- **Disk.** Raw positions are deleted after 48 hours and weather after 7 days.
  Detector windows are kept: at one row per aircraft per minute they grow
  slowly, but nothing prunes them yet.
- **Quotas.** The pollers pace themselves from `OPENSKY_DAILY_CREDITS` and
  `ADSBLOL_DAILY_REQUESTS`. Running a second copy of the stack with the same
  credentials doubles the spend.
