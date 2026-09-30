import { useState } from "react";
import { Route, Routes } from "react-router-dom";
import "./App.css";
import { startCheckout } from "@/lib/billing";
import { getMarketplaceUrl, getPrimaryInstallUrl, isMarketplaceLive } from "@/lib/github";
import SuccessPage from "@/components/SuccessPage";
import CancelPage from "@/components/CancelPage";
import GithubInstalledPage from "@/components/GithubInstalledPage";

const plans = [
  { name: "Free", price: "$0", note: "25 tasks each month · no credit card", features: ["Preview-first task loop", "One workspace", "30-day history"], cta: "Start free" },
  { name: "Pro", price: "$49", suffix: "/seat/month", note: "For founders and technical leads", features: ["500 tasks each month", "AI + rule fallback", "Reports, ROI and PR previews"], cta: "Start Pro", featured: true, planKey: "pro" },
  { name: "Team", price: "$299", suffix: "/workspace/month", note: "10 seats included", features: ["5,000 tasks each month", "GitHub, Slack and Linear hooks", "Audit export and priority support"], cta: "Get Team", planKey: "team" },
  { name: "Enterprise", price: "From $2,000", suffix: "/month", note: "For controlled or private environments", features: ["Self-hosted deployment", "SSO and company rules", "Dedicated onboarding"], cta: "Talk to Manda" },
];

const email = "manda@empire1.cloud";

// Plan selection + billing for Free/Pro/Team happens on GitHub's own
// Marketplace listing page once it's live (GitHub hosts that UI, not us —
// see omni_agent/sales/pricing.md and DEPLOY.md for why). Until then, the
// Stripe checkout built earlier stays as a direct-sale fallback: Marketplace
// requires 100 installs before a paid plan can even go live, so early
// customers need a way to pay before that threshold is hit. Enterprise
// stays sales-assisted (mailto) either way — that's not a Marketplace deal.
function PlanCard({ plan }) {
  const [status, setStatus] = useState("idle"); // idle | loading | error
  const [error, setError] = useState(null);
  const marketplaceUrl = getMarketplaceUrl();

  if (!plan.planKey) {
    return (
      <article className={plan.featured ? "featured" : ""}>
        {plan.featured && <small>BEST START</small>}
        <h3>{plan.name}</h3>
        <div className="price">{plan.price}<span>{plan.suffix}</span></div>
        <p>{plan.note}</p>
        <ul>{plan.features.map((f) => <li key={f}>{f}</li>)}</ul>
        <a href={`mailto:${email}?subject=${encodeURIComponent(`Omni-Agent ${plan.name}`)}`}>{plan.cta}</a>
      </article>
    );
  }

  const handleBuy = async () => {
    setStatus("loading");
    setError(null);
    try {
      const { checkoutUrl } = await startCheckout(plan.planKey);
      window.location.href = checkoutUrl;
    } catch (err) {
      setStatus("error");
      setError(err.message);
    }
  };

  return (
    <article className={plan.featured ? "featured" : ""}>
      {plan.featured && <small>BEST START</small>}
      <h3>{plan.name}</h3>
      <div className="price">{plan.price}<span>{plan.suffix}</span></div>
      <p>{plan.note}</p>
      <ul>{plan.features.map((f) => <li key={f}>{f}</li>)}</ul>
      {marketplaceUrl ? (
        <a href={marketplaceUrl} target="_blank" rel="noreferrer">
          View on GitHub Marketplace
        </a>
      ) : (
        <button type="button" onClick={handleBuy} disabled={status === "loading"}>
          {status === "loading" ? "Redirecting…" : plan.cta}
        </button>
      )}
      {status === "error" && <p className="checkoutError">{error}</p>}
      {!marketplaceUrl && (
        <p className="planNote">Also coming to GitHub Marketplace.</p>
      )}
    </article>
  );
}

const receipt = `$ omni-agent run-next --apply

About to write:
  create   src/textkit/case.py
Write these files? [y/N] y

$ omni-agent explain TASK-29ddc774
TASK-29ddc774 — Done — verified
WHAT CHANGED        create src/textkit/case.py
WHAT WAS CHECKED    tests: pass · lint: pass · 3/3 criteria
WHAT WAS BLOCKED    nothing
WHAT WAS PROTECTED  no protected path was requested
WHO APPROVED        approved by ana`;

const examples = [
  ["Backlog note → validated fix", "“Add a usage section to the docs” becomes a scoped change that is tested, scored and comes with a receipt."],
  ["Small refactors, fewer tabs", "Scope the change to the files named in the task, then run your tests, without opening ten files by hand."],
  ["Safe tasks overnight", "Queue low-risk maintenance as previews and review the exact file lists in the morning."],
  ["Warned before risky paths", "Writes to .env, keys, secrets and CI are refused, and the receipt says so."],
];

const proof = [
  ["What changed", "Every file created or edited, per task."],
  ["Why", "The task, its scope and the acceptance criteria it had to meet."],
  ["What passed", "Tests, lint and criteria, scored before anything is marked done."],
  ["What was blocked", "Tasks that stopped for missing context, and the reason why."],
  ["What was protected", "Writes the guardrails refused, and who approved the rest."],
];

function Home() {
  const primaryInstallUrl = getPrimaryInstallUrl();
  const marketplaceLive = isMarketplaceLive();

  return (
    <main>
      <nav><a className="brand" href="#top">OMNI<span>AGENT</span></a><div><a href="#demo">How it works</a><a href="#safe">Safety</a><a href="#pricing">Pricing</a><a className="navCta" href={`mailto:${email}?subject=Omni-Agent demo`}>Book demo</a></div></nav>
      <section className="hero" id="top">
        <p className="eyebrow">
          {marketplaceLive ? "ON GITHUB MARKETPLACE" : "SAFE TASK EXECUTION FOR CODE MAINTENANCE"}
        </p>
        <h1>Turn backlog tasks into <em>verified code changes.</em></h1>
        <p className="lead">With guardrails. Omni-Agent previews every change, writes only where you allow it, runs your tests, and keeps a receipt of what changed, what passed, what was blocked and what it refused to touch.</p>
        <div className="actions">
          {primaryInstallUrl ? (
            <a className="primary" href={primaryInstallUrl} target="_blank" rel="noreferrer">
              {marketplaceLive ? "Install via GitHub Marketplace" : "Install the GitHub App"}
            </a>
          ) : (
            <a className="primary" href={`mailto:${email}?subject=Start Omni-Agent free`}>Start free</a>
          )}
          <a className="secondary" href="#demo">See a task start to finish</a>
        </div>
        <p className="trust">Runs locally · Preview before every write · Protected paths stay protected · No credit card to try</p>
      </section>

      <section className="demo" id="demo">
        <div>
          <p className="eyebrow">FROM TASK TO RECEIPT</p>
          <h2>Your first task in ten minutes.</h2>
          <ol className="steps">
            <li><b>Install and point it at a repo.</b> <code>omni-agent quickstart</code> finds the repo root, proposes safe write folders and seeds example tasks.</li>
            <li><b>Bring your backlog.</b> Import GitHub issues, a Jira or CSV export, or add a task from a template.</li>
            <li><b>Preview.</b> See exactly which files a task would touch. Nothing is written.</li>
            <li><b>Apply and verify.</b> Confirm the file list. Omni-Agent writes, tests, scores and records who approved it.</li>
          </ol>
          <p className="muted">Try it without your own code: <code>omni-agent demo</code> runs a sample repo offline, with no API key.</p>
        </div>
        <pre className="terminal" aria-label="Example run">{receipt}</pre>
      </section>

      <section className="product" id="product">
        <p className="eyebrow">WHY IT HELPS</p>
        <div className="cards four">
          {examples.map(([title, body], i) => (
            <article key={title}><b>{String(i + 1).padStart(2, "0")}</b><h2>{title}</h2><p>{body}</p></article>
          ))}
        </div>
      </section>

      <section className="proof">
        <p className="eyebrow">PROOF, NOT PROMISES</p>
        <h2>Every task leaves a receipt.</h2>
        <div className="proofGrid">
          {proof.map(([title, body]) => <div key={title}><h3>{title}</h3><p>{body}</p></div>)}
        </div>
        <p className="muted">A team dashboard rolls receipts up into the queue, blocked work, completed work, test pass rate, estimated hours saved and approvals.</p>
      </section>

      <section className="safe" id="safe">
        <div>
          <p className="eyebrow">SAFE BY DEFAULT</p>
          <h2>Nothing lands without your say-so.</h2>
          <ul>
            <li>Every run is a read-only preview until you pass <code>--apply</code></li>
            <li>You confirm the exact file list before anything is written</li>
            <li>Allowed folders are set per repo, in a file you can review</li>
            <li>.env, keys, secrets, .git and CI workflows are always protected</li>
            <li>Optional approval gate, with who approved what on record</li>
          </ul>
        </div>
        <div>
          <p className="eyebrow">PRIVATE BY DESIGN</p>
          <h2>Your code stays on your machine.</h2>
          <ul>
            <li>The task engine runs locally, against your checkout</li>
            <li>The GitHub App asks only for metadata. It never reads your source code</li>
            <li>Only allowed areas are ever modified</li>
            <li>A local evidence log records every write, test, refusal and approval</li>
          </ul>
        </div>
      </section>

      <section className="compare">
        <p className="eyebrow">BEFORE AND AFTER</p>
        <div className="tableWrap">
          <table>
            <thead><tr><th></th><th>Before</th><th>With Omni-Agent</th></tr></thead>
            <tbody>
              <tr><td>Intake</td><td>TODOs scattered across notes, issues and Jira</td><td>One task queue, imported in a command</td></tr>
              <tr><td>Scope</td><td>Someone opens files and explores</td><td>Preview lists the files first</td></tr>
              <tr><td>Change</td><td>Manual, or an AI tool with the run of the repo</td><td>Writes only inside allowed folders, after confirmation</td></tr>
              <tr><td>Verification</td><td>“Looks fine to me”</td><td>Tests, lint and criteria scored per task</td></tr>
              <tr><td>Reporting</td><td>Status meetings</td><td>Dashboard with receipts and approvals</td></tr>
            </tbody>
          </table>
        </div>
        <p className="muted">Built for small teams with repetitive repo work, internal engineering ops, repo cleanup and code maintenance. It is not built for general AI coding of everything.</p>
      </section>

      <section className="pricing" id="pricing">
        <p className="eyebrow">PRICING</p><h2>Start small. Pay when it saves work.</h2>
        <div className="plans">{plans.map((plan) => <PlanCard plan={plan} key={plan.name} />)}</div>
        <p className="fine">No credit card required for Free. Monthly plans can be cancelled anytime and stay active until the end of the current billing period. Annual plans include two months free. Team white-label reports are available for $149/month.</p>
      </section>

      <section className="pilot"><div><p className="eyebrow">14-DAY PILOT</p><h2>Give it one stale backlog.</h2><p>If Omni-Agent cannot close real tasks and produce measurable proof on your repository, you pay nothing and keep the reports.</p></div><a className="primary" href={`mailto:${email}?subject=Omni-Agent 14-day pilot`}>Start the pilot</a></section>
      <footer><div className="brand">OMNI<span>AGENT</span></div><p>Built by Empire-1 · Evidence before claims.</p><a href={`mailto:${email}`}>{email}</a></footer>
    </main>
  );
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/success" element={<SuccessPage />} />
      <Route path="/cancel" element={<CancelPage />} />
      <Route path="/github/installed" element={<GithubInstalledPage />} />
    </Routes>
  );
}

export default App;
