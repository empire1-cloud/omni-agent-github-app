# Omni-Agent — Pricing & Plans

Four tiers. Clear gates. No surprises. Prices in USD.

**Low-risk evaluation:** Free needs no credit card. It gives you 25 real task runs a month on your own repo, and every run is a preview until you pass `--apply`. Monthly paid plans can be cancelled anytime and stay active until the end of the billing period.

| | **Free** | **Pro** | **Team** | **Enterprise** |
|---|---|---|---|---|
| **Monthly price** | $0 | **$49 / seat** | **$299 / workspace** (includes 10 seats) | **Custom** — starts at $2,000 / mo |
| **Annual discount** | — | 2 months free | 2 months free | Negotiated |
| **Billing** | — | Stripe, monthly | Stripe, monthly or annual | Invoice, net-30 |

---

## Feature gates

| Capability | Free | Pro | Team | Enterprise |
|---|:-:|:-:|:-:|:-:|
| Scanner (markdown → tasks) | ✅ | ✅ | ✅ | ✅ |
| Triage + State machine (SQLite) | ✅ | ✅ | ✅ | ✅ |
| Black-box guardrails | ✅ | ✅ | ✅ | ✅ + custom policies |
| Persona mode: **rule** | ✅ | ✅ | ✅ | ✅ |
| Persona mode: **hybrid** (LLM + rule fallback) | — | ✅ (BYO key or Emergent Universal Key) | ✅ | ✅ |
| Persona mode: **llm** (LLM-first) | — | ✅ | ✅ | ✅ |
| **Client Report Generator** (MD + JSON per run) | — | ✅ | ✅ | ✅ |
| **ROI Tracker** (live, weekly, monthly) | — | ✅ | ✅ | ✅ |
| **PR Autopilot** preview | — | ✅ | ✅ | ✅ |
| Auto-commit / PR push (opt-in) | — | — | ✅ | ✅ |
| Slack / Linear / GitHub webhooks | — | — | ✅ | ✅ |
| Role-based guardrail policies | — | — | ✅ | ✅ |
| SSO (Google / Okta / Azure AD) | — | — | — | ✅ |
| Self-hosted deploy (Docker / Helm) | — | — | — | ✅ |
| Custom allowed/forbidden path rules | Config only | Config only | Config + roles | Policies-as-code |
| Audit log export (JSONL / Parquet) | — | — | ✅ | ✅ |
| Priority support | Community | Email, 48h SLA | Email + Slack, 24h SLA | 4h SLA + on-call |
| Dedicated onboarding | — | — | 1h kickoff | 14-day success sprint |

---

## Usage limits

| Limit | Free | Pro | Team | Enterprise |
|---|---|---|---|---|
| Workspaces | 1 | 1 | 5 | Unlimited |
| Concurrent CLI runs | 1 | 2 | 8 | Unlimited |
| Tasks / month (runs) | **25** | **500** | **5,000** | Unlimited |
| LLM tokens | N/A (rule only) | BYO key — no cap | BYO key — no cap | BYO key — no cap |
| History retention (SQLite audit trail) | 30 days | 12 months | 24 months | Unlimited |
| PR previews / month | 0 | Unlimited | Unlimited | Unlimited |
| Seats included | 1 | 1 (add $39 each) | 10 (add $29 each) | Custom |

---

## Ideal customer profile

| Tier | Who |
|---|---|
| **Free** | Solo operators trying Omni-Agent on a side repo. Proof-of-concept buyers. |
| **Pro** | Indie founders, solo CTOs, and tech-leads who bought an LLM key and want a real execution loop sitting on top of it. They have a messy `TODO.md` they want to close. |
| **Team** | 5–30 person engineering orgs running a shared inbox of backlog tasks across 2–5 repos. Typically Series-Seed to Series-B SaaS teams with a Linear/Jira + GitHub stack. |
| **Enterprise** | Compliance-sensitive orgs (fintech, health, defense, or any team with a black-box policy requirement). They need self-host, SSO, and policies-as-code. Also: agencies running it as managed-service for their clients. |

---

## Upgrade triggers (what makes a customer move up a tier)

| From → To | Trigger |
|---|---|
| Free → Pro | First time they hit the 25-tasks/month cap, or they want an LLM-powered Analyst/Developer/Evaluator instead of rule mode. |
| Pro → Team | Second seat requested, or they want Slack/Linear/GitHub webhooks so the team can see runs without logging into the CLI. |
| Team → Enterprise | Legal asks for SSO + audit export, or procurement requires self-host, or they want custom guardrail policies (e.g., "services X and Y may never touch schema migrations"). |

---

## What's NOT in Free

- Hybrid/LLM personas (rule-mode only).
- Client Reports, ROI Tracker, PR Autopilot.
- No history beyond 30 days.
- No priority support — GitHub issues only.

_Why:_ Free exists to prove the loop works on your repo. The three monetized modules above are the reason teams pay.

---

## Add-ons (any tier)

| Add-on | Price |
|---|---|
| Additional seats (Pro) | $39 / seat / month |
| Additional seats (Team) | $29 / seat / month |
| Additional workspace (Team) | $79 / workspace / month |
| White-label client reports (Team+) | $149 / month |
| Dedicated success engineer (Enterprise) | From $3,500 / month |
