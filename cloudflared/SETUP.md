# Cloudflare Named Tunnel — Setup Guide

## One-time setup (~10 minutes)

### 1. Install cloudflared locally (just for setup)
```bash
# macOS
brew install cloudflare/cloudflare/cloudflared
# Linux
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o cloudflared && chmod +x cloudflared && sudo mv cloudflared /usr/local/bin/
```

### 2. Authenticate and create the tunnel
```bash
cloudflared tunnel login          # opens browser → authorise your domain
cloudflared tunnel create remote-vibes
```
This prints a **Tunnel UUID** and saves credentials to:
`~/.cloudflared/<uuid>.json`

Copy that file here:
```bash
cp ~/.cloudflared/<uuid>.json ./cloudflared/creds.json
```

### 3. Add wildcard DNS in Cloudflare dashboard
In your zone DNS settings add two records (both **Proxied**):

| Type  | Name                  | Content                                    |
|-------|-----------------------|--------------------------------------------|
| CNAME | `remote.ilmal.se`     | `<uuid>.cfargotunnel.com`                  |
| CNAME | `*.remote.ilmal.se`   | `<uuid>.cfargotunnel.com`                  |

Replace `remote.ilmal.se` with your actual domain.

### 4. Set .env variables
```env
CF_TUNNEL_ID=<your-tunnel-uuid>
CF_TUNNEL_DOMAIN=remote.ilmal.se
```

### 5. Start the stack
```bash
docker compose up -d
```

cloudflared will start and route:
- `remote.ilmal.se`               → Remote Vibes dashboard
- `{repo}.remote.ilmal.se`        → repo's dev/app server
- `edit.{repo}.remote.ilmal.se`   → code-server for that repo
- `app.{repo}.remote.ilmal.se`    → alias for dev server

Subdomains are created/removed automatically as you start/stop sessions.

## Troubleshooting
```bash
docker compose logs cloudflared -f
```
