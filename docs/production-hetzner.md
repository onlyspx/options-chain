# Production Deployment On Hetzner

This app can run on one VPS exactly like it ran on Vercel:

- FastAPI serves the API
- FastAPI also serves the built frontend from `web/frontend/dist`
- Nginx proxies `spx0.com` to the local app

Recommended target:

- Provider: Hetzner Cloud
- Region: Hillsboro
- Size: CPX21
- OS: Ubuntu 24.04 LTS
- DNS: Cloudflare

## 1. Provision The VPS

Create one Ubuntu 24.04 server and attach your SSH key.

After first login:

```bash
sudo apt update
sudo apt install -y git nginx python3 python3-venv python3-pip ufw ca-certificates curl certbot python3-certbot-nginx
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
```

Create the deploy user and app directories:

```bash
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG sudo deploy
sudo mkdir -p /srv/spx0/current /srv/spx0/shared
sudo chown -R deploy:deploy /srv/spx0
```

## 2. Clone The Repo

As the `deploy` user:

```bash
git clone <your-repo-url> /srv/spx0/current
cd /srv/spx0/current
git checkout feat/spx0-prod
```

## 3. Add Environment Variables

Copy the example file and fill in the live values:

```bash
cp /srv/spx0/current/deploy/env/spx0.env.example /srv/spx0/shared/.env
chmod 600 /srv/spx0/shared/.env
```

Required values for the current site:

- `PUBLIC_COM_SECRET`
- `PUBLIC_COM_ACCOUNT_ID`

`DISCORD_WEBHOOK_URL` is optional and only needed for the existing Discord daemon scripts.

## 4. First App Build

Run the deploy helper once:

```bash
cd /srv/spx0/current
APP_ROOT=/srv/spx0/current VENV_DIR=/srv/spx0/shared/venv bash deploy/bin/redeploy.sh
```

If the service is not installed yet, the script will skip the restart step on first boot.

## 5. Install The systemd Service

Copy the service file into systemd:

```bash
sudo cp /srv/spx0/current/deploy/systemd/spx0-web.service /etc/systemd/system/spx0-web.service
sudo systemctl daemon-reload
sudo systemctl enable spx0-web.service
sudo systemctl start spx0-web.service
sudo systemctl status spx0-web.service --no-pager
```

Logs:

```bash
journalctl -u spx0-web.service -f
```

## 6. Install Bootstrap Nginx

Install the temporary HTTP config first. This lets the site respond on port 80 so Let's Encrypt can validate the domain.

```bash
sudo cp /srv/spx0/current/deploy/nginx/spx0-bootstrap-http.conf /etc/nginx/sites-available/spx0.com.conf
sudo ln -sf /etc/nginx/sites-available/spx0.com.conf /etc/nginx/sites-enabled/spx0.com.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

## 7. Point `spx0.com` At Cloudflare

At the registrar:

- switch nameservers to Cloudflare

In Cloudflare DNS:

- `A` record: `spx0.com` -> your VPS IP
- `CNAME`: `www` -> `spx0.com`
- keep both records **DNS only** until HTTPS is issued

Wait until these commands return your live records:

```bash
dig +short A spx0.com
dig +short CNAME www.spx0.com
```

## 8. Issue Let's Encrypt

Once DNS resolves, request the certificate:

```bash
sudo certbot --nginx -d spx0.com -d www.spx0.com --non-interactive --agree-tos --register-unsafely-without-email --redirect
```

Certbot will place the certificate at:

- `/etc/letsencrypt/live/spx0.com/fullchain.pem`
- `/etc/letsencrypt/live/spx0.com/privkey.pem`

## 9. Install Final Nginx

Replace the bootstrap config with the final HTTPS config:

```bash
sudo cp /srv/spx0/current/deploy/nginx/spx0.com.conf /etc/nginx/sites-available/spx0.com.conf
sudo ln -sf /etc/nginx/sites-available/spx0.com.conf /etc/nginx/sites-enabled/spx0.com.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

## 10. Optional Cloudflare Proxy

After HTTPS works directly on the VPS, you can turn on the orange cloud in Cloudflare.

Recommended Cloudflare settings after proxying:

- proxy both `spx0.com` and `www`
- SSL/TLS mode: `Full (strict)`

## 11. Lock Down The VPS

Recommended firewall:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Also disable password SSH login once key-based login is confirmed.

## 12. Normal Deploy Flow

For each deploy:

```bash
cd /srv/spx0/current
APP_ROOT=/srv/spx0/current VENV_DIR=/srv/spx0/shared/venv bash deploy/bin/redeploy.sh
```

That flow will:

- pull `feat/spx0-prod`
- install Python dependencies
- run `npm install` in `web/frontend`
- install the matching Linux Rollup native package when needed
- build the frontend
- restart `spx0-web.service`

## 13. Smoke Checks

After DNS and Nginx are live, verify:

```bash
curl -I https://spx0.com
curl https://spx0.com/api/snapshot
```

The app should behave the same way it did on Vercel:

- dashboard loads at `/`
- API responds at `/api/snapshot`
- no separate frontend host is required
- `www.spx0.com` redirects to `https://spx0.com`
