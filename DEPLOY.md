# Omni-Agent — Last Mile to First Dollar

The original six stacked launch PRs are merged. This runbook now covers
the remaining live-service configuration plus V2 autonomous PR posting.
Merging code does not deploy it or connect real Stripe/GitHub credentials;
those founder-controlled production taps remain below.

**Primary target: a paid GitHub App listed on the GitHub Marketplace**
(product direction). Marketplace requires ~100 installs and a verified
publisher before a paid plan can go live — see `MARKETPLACE.md` for that
full sequence. The Stripe checkout built first stays as the **direct-sale
bridge**: the fastest way to take a real first payment while the app
builds toward Marketplace eligibility. Both paths are live in the code at
once; the landing page shows whichever is configured (see
`frontend/src/lib/github.js`).

## 1. Merged foundation — complete

The six launch layers are now on `main`: billing/health, checkout,
Render deployment, GitHub App core, Marketplace-aware frontend CTAs, and
Marketplace deployment documentation. V2 adds the evaluated-task →
evidence preview → authenticated PR → check-run completion loop.

## 2. Deploy (Render, via the included blueprint)

1. Render dashboard → New → Blueprint → point at this repo → reads
   `render.yaml` → two services: `omni-agent-api`, `omni-agent-web`.
2. Fill in the `sync: false` env vars it prompts for (nothing here is a
   live credential yet — they're all blank until you do this):
   - `omni-agent-api`: `CORS_ORIGINS`, `APP_BASE_URL`, `FRONTEND_BASE_URL`
     (fill both in after the first deploy, once you know the URLs, then
     redeploy), `MONGO_URL` (optional), plus the Stripe and GitHub App
     vars below once you have them. Also set `OMNI_AGENT_INTERNAL_TOKEN`
     to a long random value; the local orchestrator must use the same value
     when it calls the autonomous PR endpoint.
   - `omni-agent-web`: `REACT_APP_BACKEND_URL`.
3. **Apply** — this is the actual go-live deploy tap.
4. `curl https://<api-domain>/api/health` → confirm `200`, everything
   reports `false`/unconfigured except what you've actually set.

### Vercel (alternative host for the landing page)

The existing Vercel project `empire1-lyrica-ecosystem` has its **Root Directory set to `backend`**. It serves only the FastAPI API, so `/api/health` answers and `/` returns `{"detail":"Not Found"}`. The landing page in `frontend/` is not deployed there.

To publish the landing page, add a second Vercel project from this repo:

1. Vercel → Add New → Project → import `empire1-cloud/omni-agent-github-app`.
2. Set **Root Directory** to `frontend`. `frontend/vercel.json` supplies the install and build commands, the `build` output folder, and SPA rewrites.
3. Add the build-time env vars from `frontend/.env.example`. At minimum, set `REACT_APP_BACKEND_URL` to the API deployment URL so Stripe checkout works.
4. On the API project, set `CORS_ORIGINS` to the new frontend URL.
5. Previews are behind Vercel Authentication by default. Turn off Deployment Protection for production if the page should be public.

## 3. Get a first dollar via Stripe (the bridge, works today)

1. Stripe → create Pro ($49/mo) and Team ($299/mo) products, test mode
   first. Copy Price IDs, secret key, webhook signing secret.
2. Set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO`,
   `STRIPE_PRICE_TEAM` on `omni-agent-api`, redeploy.
3. Click "Start Pro" on the live site, complete a **test-mode** checkout
   with Stripe's `4242 4242 4242 4242` card, confirm the webhook records
   it (`backend/data/customers.jsonl` or `db.billing_events`).
4. Swap to live keys when ready for a real charge.

## 4. Stand up the GitHub App

1. Visit `https://<api-domain>/api/github/app/new`, review the manifest,
   confirm on GitHub — this creates the real App.
2. Copy the one-time-shown credentials into `GITHUB_APP_ID`,
   `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_CLIENT_ID`,
   `GITHUB_CLIENT_SECRET`, redeploy.
3. Set `REACT_APP_GITHUB_APP_INSTALL_URL` on `omni-agent-web` to
   `https://github.com/apps/<slug>` — works immediately, before any
   Marketplace listing exists.
4. Install it on a test repo yourself, confirm
   `backend/data/github_installations.jsonl` records it.
5. Configure V2 PR posting in `omni_agent/config.yaml`:
   - set `github.owner`, `github.repo`, and `github.installation_id`;
   - set `OMNI_AGENT_API_URL` to the deployed API URL;
   - set `OMNI_AGENT_INTERNAL_TOKEN` to the same private value used by
     `omni-agent-api`;
   - set `OMNI_AGENT_HEAD_BRANCH` to the already-pushed task branch, or
     use the configured `omni-agent/<task_id>` branch convention.
6. Run one evaluated task. A successful `done` result must now include the
   GitHub PR URL; the same URL is persisted as a `github_pull_request`
   artifact and rendered in the client report. The endpoint fails closed
   with 401/503 when its caller token is missing or invalid.

V2 opens a PR from an existing remote head branch; it does not silently
push local changes or write through the GitHub App's `contents` permission.
That keeps branch publication under the repository's existing credential
and approval boundary.

## 5. List on GitHub Marketplace

Full sequenced checklist — publisher verification, 100-install
threshold, listing content, pricing plans, GitHub's review — in
**`MARKETPLACE.md`**. Once the listing is live, set
`REACT_APP_GITHUB_MARKETPLACE_URL` and the site's CTAs switch to it
automatically.

## 6. DNS (optional, your call)

Point a real domain at the Render services via Render's custom domain
settings. Not required to take a first payment — the `*.onrender.com`
URLs work for both Stripe checkout and the GitHub App's URLs.

## 7. First customer

The GTM assets already exist and don't need engineering work:
- `omni_agent/sales/outreach_kit.md` — outreach copy
- `omni_agent/sales/demo_script.md` — demo script
- `omni_agent/sales/pilot_program.md` — 14-day pilot structure + close
- `omni_agent/sales/offer_sheet.md` — one-pager

Send the first outreach once steps 1–3 are done — Stripe direct-sale
doesn't wait on the Marketplace listing.
