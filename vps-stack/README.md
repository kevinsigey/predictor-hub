# vps-stack

Everything that runs on the VPS (OVHcloud VPS-1): the five predictor sites, the
NFL/CFB frontend, the predictor-hub landing page and, later, the Algo Trade
Hub. It replaces the Azure Container Apps setup before the Azure for Students
credit ends (**around 2026-10-27**).

```
                    ┌───────────────────── VPS (Ubuntu 24.04, 4 GB) ─────────────────────┐
 browser ──https──► │ caddy :443 ─┬─ <domain>, www       → sites/hub (static)           │
                    │             ├─ pl.<domain>         → pl:8000                      │
                    │             ├─ f1.<domain>         → f1:8000                      │
                    │             ├─ nba.<domain>        → nba:8000                     │
                    │             ├─ sports.<domain>     → sports:80                    │
                    │             │     /nfl/* → nfl:8001    /cfb/* → cfb:8003          │
                    │             ├─ nfl.<domain>, cfb.<domain> → the APIs directly     │
                    │             └─ trade.<domain>      → tradehub:8000 (later)        │
                    └─────────────────────────────────────────────────────────────────────┘
 GitHub Actions ──ssh deploy@vps "deploy <svc> <sha>"──► bin/deploy (pull, restart, health-check, roll back)
```

Snapshot refreshes are unchanged: each predictor's `refresh-public-snapshot`
GitHub Action commits a new `public_snapshot.json`, and the running app polls
it from raw.githubusercontent.com every 5 minutes. No redeploy is needed for new data.

Measured locally on 2026-09-25 with all images running: about **1.7 GB** of
RAM in total (cfb 415 MB, f1 376, nba 328, nfl 246, pl 245, caddy 44, sports 64).

| File | What |
|---|---|
| `compose.yml` | All services. Only Caddy publishes ports. Image tags come from `.env`. |
| `Caddyfile` | Hostname → service routing. Caddy obtains HTTPS certificates automatically. |
| `.env.example` | Domain, image tags, API keys. Copied to `/opt/stack/.env` on the server. |
| `bin/bootstrap.sh` | One-time server setup: Docker, swap, firewall, `deploy` user, SSH hardening. |
| `bin/deploy` | The only thing CI can run on the server (forced SSH command). |
| `bin/pull-azure-volumes.sh` | Copies the NBA/NFL/CFB Azure file shares to the VPS. |
| `bin/set-github-secrets.sh` | Sets `VPS_HOST`, `VPS_SSH_KEY`, `VPS_KNOWN_HOSTS` in all 7 repos. |
| `bin/tradehub-job`, `systemd/` | Hourly trade hub scan/settle (installed, off until the trade hub ships). |
| `bin/nba-db-backup`, `systemd/nba-db-backup.*` | Daily 12:30 UTC: integrity-check the NBA tracking DB and copy it to `volumes/nba-tracking/backups/`, keeping 14 (two a day: the rescore service also pulls the backup in first, so a catch-up run after downtime is covered). Runs before the 13:40 rescore that writes it. |

---

## 1. Buy the server and a domain

1. **Server:** OVHcloud VPS-1 (2 vCore / 4 GB / 40 GB NVMe, about $5.35/month), US East, image **Ubuntu 24.04** under "Distribution only", no commitment, keep the free daily backup. Add your Mac's SSH public key when asked (`cat ~/.ssh/id_ed25519_openclaw.pub` on this Mac). It must be x86, not ARM, because the CI images are amd64; all OVH VPS plans are x86. The scripts also work on a Hetzner CX22/CX23; there you log in as `root` instead of `ubuntu`.
2. **Domain:** any registrar (Cloudflare Registrar and Porkbun are at cost, about $10/year). Add two DNS records pointing at the server's IPv4:
   - `A  @  <ip>`
   - `A  *  <ip>` (the wildcard covers pl., f1., nba., sports., nfl., cfb., trade., www.)

   If you use Cloudflare DNS, keep the records **DNS only** (grey cloud), at least at first, so Caddy can get its certificates.

   No domain yet? Set `DOMAIN=<ip-with-dashes>.sslip.io` (e.g. `203-0-113-7.sslip.io`) to test. sslip.io shares Let's Encrypt rate limits with everyone who uses it, so don't go live on it.

## 2. First-time setup (about 15 minutes)

On OVH you log in as `ubuntu` (it has passwordless sudo). Root login is blocked. On your Mac:

```bash
# CI deploy key (no passphrase: GitHub Actions uses it)
ssh-keygen -t ed25519 -f ~/.ssh/vps_ci_deploy -N "" -C "github-actions-deploy"

# copy this folder (via sudo, since /opt is root-owned) and the CI public key
rsync -av --rsync-path="sudo rsync" --exclude .env --exclude volumes --exclude azure-volumes ~/Documents/Projects.nosync/vps-stack/ ubuntu@<ip>:/opt/stack/
scp ~/.ssh/vps_ci_deploy.pub ubuntu@<ip>:/tmp/ci_deploy.pub
ssh ubuntu@<ip> 'sudo bash /opt/stack/bin/bootstrap.sh /tmp/ci_deploy.pub'
```

Bootstrap prints the server's host-key fingerprint at the end. Keep it for step 4.

Fill in the secrets. The values are the ones currently stored in Azure; print them with:

```bash
az containerapp secret list -n cfb-predictor -g predictor-hub-rg --show-values -o table   # odds, sportsbook, cfbd
az containerapp secret list -n pl-predictor  -g predictor-hub-rg --show-values -o table   # github-actions-token
```

```bash
ssh deploy@<ip>
nano /opt/stack/.env        # DOMAIN, ODDS_API_KEY, SPORTSBOOK_API_KEY, CFBD_API_KEY, PL_GITHUB_ACTIONS_TOKEN
cd /opt/stack && docker compose pull && docker compose up -d
bin/deploy hub "$(git ls-remote https://github.com/Kevocado/predictor-hub.git HEAD | cut -f1)"
docker compose ps
```

## 3. Move the Azure data (NBA track record, NFL/CFB caches)

The NBA app keeps its track record (`tracking.db`) on an Azure file share, and
NFL/CFB keep caches on shares too. PL and F1 have no volumes. On your Mac, logged into `az`:

```bash
~/Documents/Projects.nosync/vps-stack/bin/pull-azure-volumes.sh <ip>
```

Run it again right before switching off Azure (step 6) so the newest records come along.

## 4. Check the sites

Open each one: `https://<domain>`, `https://pl.<domain>`, `https://f1.<domain>`,
`https://nba.<domain>`, `https://sports.<domain>` (check both the NFL and CFB tabs load data).

If a certificate doesn't appear, run `docker compose logs caddy`. The usual causes are DNS not pointing at the server yet, or ports 80/443 blocked by a provider-side firewall.

## 5. Point CI at the VPS

Each repo's deploy workflow now has a `vps` job next to the Azure steps. It
stays off until the repo has a `VPS_HOST` variable. Changed and **not yet committed** in:

- PL_Predictor, F1_Predictor, NBA_Predictor, NFL_Predictor, CFB_Predictor, Sports_Predictor: `.github/workflows/deploy-azure*.yml`
- predictor-hub: its workflow, plus `index.html` (the card links follow whatever domain serves the page)
- Sports_Predictor: `src/api/client.ts` (on any non-Azure host it calls `/nfl/api` and `/cfb/api` on the same origin)

Commit and push those, then:

```bash
~/Documents/Projects.nosync/vps-stack/bin/set-github-secrets.sh <ip> ~/.ssh/vps_ci_deploy
```

From then on every push deploys to **both** Azure and the VPS. To check the VPS path, re-run one workflow
(Actions → Run workflow, or push a trivial commit) and watch the `vps` job.

## 6. Cut over (before ~2026-10-27)

1. Run `bin/pull-azure-volumes.sh <ip>` one last time.
2. `bin/set-github-secrets.sh --disable-azure` stops the Azure steps in all workflows.
3. Delete the Azure resources. **This can't be undone**, so check the VPS sites first:
   `az group delete -n predictor-hub-rg` (container apps, file shares, static web app).
4. Later, clean up: delete the Azure steps from the workflows and the Azure branch in `Sports_Predictor/src/api/client.ts`, and point `Sports_Predictor/scripts/build-team-logos.py` at `https://nfl.<domain>` / `https://cfb.<domain>`.

## Day-to-day

```bash
ssh deploy@<ip>
cd /opt/stack
bin/deploy status                        # what's running
docker compose logs -f --tail 100 nba    # logs
bin/deploy deploy pl <older-git-sha>     # roll back one site
bin/deploy deploy pl latest              # newest image
docker stats --no-stream                 # memory
```

- **Editing `compose.yml` or `Caddyfile`:** edit the copy on your Mac, then
  `rsync -av --exclude .env --exclude volumes --exclude azure-volumes --exclude sites ~/Documents/Projects.nosync/vps-stack/ deploy@<ip>:/opt/stack/`
  and on the server `docker compose up -d` (plus `docker compose restart caddy` for Caddyfile changes). Consider pushing this folder to a private GitHub repo so it's backed up.
- **OS updates** install automatically (unattended-upgrades). Reboot now and then: `sudo reboot`. Every container restarts on its own.
- **Tracking databases:** NBA/NFL/CFB data lives in `/opt/stack/volumes/` and survives redeploys. PL and F1 behave as they did on Azure (their track record ships in the image and is kept current by their GitHub Actions).
- **NBA tracking DB backups:** `systemd/nba-db-backup.timer` takes a verified copy every day at 12:30 UTC into `volumes/nba-tracking/backups/` (14 kept). If it fails, `systemctl --failed` says so — that check is the early warning for the corruption that took the games page down on 2026-10-26. Restore with `cp volumes/nba-tracking/backups/tracking-<stamp>.db volumes/nba-tracking/tracking.db` while the `nba` container is stopped.

## Algo Trade Hub (later)

The `tradehub` service (compose profile `tradehub`) and the two systemd timers
are already here, switched off. Turning them on is rollout step 5, rewritten for this VPS in
`algo-trade-hub-prod/docs/superpowers/plans/2026-09-25-vps-deploy.md`. Its
Task 5 is the checklist: Supabase keys in `.env`, first deploy, uncomment the
`trade.` block in the Caddyfile, `systemctl enable --now tradehub-scan.timer tradehub-settle.timer`.
The Health page's Re-run button (phase-2 §4.3) additionally needs
`systemctl enable --now tradehub-rerun.timer`: the API drops a trigger file in
`volumes/tradehub-cache/rerun/`, and `bin/tradehub-rerun` claims it within a minute
(archived under `rerun/done/`, which is the audit record) and runs the job.
