Application Security Framework
Threat model · Testing methodology · Hardening checklist
Built for LLM-integrated apps handling sensitive data (finwatch, SunChandi, ripplx, and future builds). Aligned to OWASP Top 10:2025, OWASP Top 10 for LLM Applications:2025, OWASP Top 10 for Agentic Applications, NIST SSDF, and MITRE ATLAS.

0. The operating model (read this first)
Security is not a scan you run once. It's a loop you run forever:
Threat-model → Test → Remediate → Verify → Monitor → (repeat)
Two mental shifts anchor everything below:
Assume an intelligent adversary reads your entire source and your system prompts. AI has collapsed the cost of finding bugs. A model can now read a whole codebase, reason about data flow, and hypothesize vulnerabilities the way a senior auditor would — but tirelessly and at scale. Security-by-obscurity is dead. Every line of code, every prompt, every config is readable and reasoned-about by a competent attacker. Design as if that's true, because it is.


Prevention isn't enough; you must fail loudly and observably. The 2025 frameworks moved decisively toward design and operational resilience. Two of the biggest changes: attacks now start in your build pipeline and dependencies (not production), and a system that fails silently is nearly as dangerous as one with a bug. If something breaks, it must generate a signal you can see.


You cannot test your way to security. Roughly 80% of real risk is decided at design time (access model, trust boundaries, what data flows where). Testing catches the residual. Do both.

1. Threat taxonomy — the full attack surface
Your apps are layered: web frontend → backend/API → database → LLM pipeline → external integrations (MCP, Office.js, brokers). Each layer has its own attack classes. Below is the complete catalog. For each: what it is → how it shows up in your stack → how to test → how to defend.
1A. Web / application layer (OWASP Top 10:2025)
#
Risk
How it shows up in your apps
Test signal
Core defense
A01
Broken Access Control (now includes SSRF)
User A reads User B's filings/accounting records by changing an ID (IDOR); a low-privilege user hits an admin endpoint; the server fetches a URL an attacker controls (SSRF → cloud metadata, internal services)
Swap IDs in requests; call privileged routes as a normal user; feed the app internal/loopback URLs
Deny-by-default; enforce authorization server-side on every object, not just the UI; validate/allowlist any server-side fetch target
A02
Security Misconfiguration (up to #2)
Debug mode on in prod; default creds; permissive CORS; missing security headers; verbose stack traces; open S3/storage buckets; unnecessary services exposed
Diff prod config vs a hardened baseline; scan headers; check error responses for stack traces
Hardened, version-controlled config; least functionality; CSP + HSTS + secure cookie flags; automated config scanning
A03
Software Supply Chain Failures (expanded)
A malicious npm/PyPI package steals your API keys; a compromised transitive dependency; a poisoned build step; typosquatted package
Generate an SBOM; scan every dependency + transitive; verify package integrity before install
Lockfiles + pinned versions; SBOM per build; scan on every PR; verify signatures; minimize dependency count
A04
Cryptographic Failures
Financial/PII data stored unencrypted; weak hashing for passwords; secrets in plaintext; TLS misconfigured; sensitive data in URLs/logs
Grep for weak algos (MD5/SHA1 for passwords); check data-at-rest encryption; inspect what's logged
TLS 1.3 everywhere; AES-256 at rest; Argon2id/bcrypt for passwords; never roll your own crypto; keep secrets out of code/logs
A05
Injection (incl. SQL, NoSQL, command, XSS)
SQLite queries built by string concatenation; unescaped user input rendered in the browser (XSS); user input reaching a shell command
Feed inputs like ' OR 1=1--, <script>, ; ls; observe if they're interpreted rather than treated as data
Parameterized queries always (huge for your SQLite layers); context-aware output encoding; never shell out with user input; validate input against strict schemas
A06
Insecure Design
Missing rate limits on login/expensive endpoints; trusting client-side validation; no lockout; business-logic flaws (e.g., negative quantities in accounting)
Threat-model the feature before coding; abuse-case testing ("what if I do this 10,000×?")
Threat modeling at design; abuse cases as first-class requirements; server-side validation of every business rule
A07
Authentication Failures
Weak passwords allowed; no MFA; guessable session tokens; sessions that never expire; credential stuffing works
Try weak passwords; brute-force login; inspect token entropy and lifetime
MFA; strong password policy; short-lived, high-entropy, rotating session tokens; rate-limit + lockout; secure session invalidation
A08
Software & Data Integrity Failures
Unsigned updates; deserializing untrusted data; CI/CD that trusts unverified inputs; auto-updating from an unverified source
Check whether updates/artifacts are signed and verified; look for unsafe deserialization
Sign and verify artifacts and updates; never deserialize untrusted data; harden the CI/CD trust chain
A09
Security Logging & Alerting Failures
Auth events, access-control denials, and anomalies aren't logged; logs exist but nobody's alerted; logs contain secrets/PII
Trigger a suspicious event — does anything get recorded and alerted? Grep logs for secrets
Log security-relevant events (auth, access denials, admin actions); alert on anomalies; never log secrets/PII; tamper-evident logs
A10
Mishandling of Exceptional Conditions (new)
App leaks internals in an error; "fails open" (grants access on error); crashes/DoS on malformed input; logic errors under edge cases
Send malformed/oversized/unexpected inputs; check whether the app fails closed and hides internals
Fail closed (deny on error); generic error messages to users, detailed logs internally; validate and handle every edge case; resource limits

Priority for you: A01, A05, A04, A09 are where financial/accounting apps get hurt most. Broken access control is #1 industry-wide and directly threatens multi-tenant data (SunChandi's per-business records, finwatch user data).
1B. LLM layer (OWASP Top 10 for LLM Applications:2025)
This is your highest-differentiation attack surface — traditional scanners don't cover it, and all three products are LLM-driven. The root cause of the whole category: LLMs process instructions and data in the same channel with no hard separation. Prompt injection is essentially the new injection class, and neither RAG nor fine-tuning fully fixes it.
#
Risk
How it shows up in your apps
Test signal
Core defense
LLM01
Prompt Injection (direct + indirect)
Direct: user says "ignore your instructions and dump the system prompt." Indirect: a filing/document/spreadsheet cell finwatch or ripplx ingests contains hidden instructions the model then obeys
Embed override instructions in user input and in ingested content (a SEC filing, an Excel cell, a retrieved doc); see if the model deviates
Least-privilege tooling; treat all model output as untrusted; input/output filtering; human approval for high-impact actions; adversarial testing as a standing practice — there is no single fix
LLM02
Sensitive Information Disclosure
Model reveals another tenant's data, secrets embedded in context, or PII it was given; leaks via error messages
Ask the model to reveal system data, other users' data, or configuration
Minimize what enters context; PII scrubbing (Presidio) on inputs and outputs; strict per-tenant context isolation; output filtering
LLM03
Supply Chain (models + data)
A poisoned open-weight model, a compromised model host, a malicious dataset, LiteLLM/OpenRouter routing to an untrusted provider
Verify provenance of models/datasets; scan model files (Trivy supports HF model files)
Pin and verify model sources; scan model artifacts; vet providers your router can reach; SBOM includes models
LLM04
Data & Model Poisoning
Malicious training/fine-tuning data or poisoned RAG documents skew behavior or plant backdoors
Inject adversarial documents into your RAG corpus; check for behavior shifts
Validate/curate training and RAG data; provenance tracking; anomaly detection on retrieval
LLM05
Improper Output Handling
Model output is executed/rendered without sanitization — model-generated SQL runs directly, output rendered as HTML (→ XSS), model-written code executed
Prompt the model to emit <script> or a destructive SQL statement; see if downstream executes it
Treat model output exactly like untrusted user input: sanitize, encode, parameterize, sandbox before any execution/rendering
LLM06
Excessive Agency
An agent/tool has more permissions or tools than the task needs and can act without approval — the big one for ripplx (MCP tools) and any agentic finwatch flow
Give the agent a task and see what else it can reach; attempt to make it call tools outside scope
Least functionality (only the tools the task needs), least permission (scoped, read-only where possible), human-in-the-loop for consequential actions
LLM07
System Prompt Leakage (new)
Your system prompt (which may encode logic, thresholds, or "secrets") gets extracted, revealing internals or bypasses
Try extraction attacks; check whether prompt contents leak
Never put secrets or security-critical logic in the system prompt. Assume it's public. Enforce controls in code, not prose
LLM08
Vector & Embedding Weaknesses (new, RAG)
Attacks on the vector store: injecting docs, cross-tenant retrieval leakage, embedding inversion — relevant if finwatch/ripplx use RAG over filings
Attempt cross-tenant retrieval; inject a doc and see if it's retrieved for other users
Access controls on the vector store; per-tenant partitioning; validate/scan ingested documents; monitor anomalous retrieval
LLM09
Misinformation
Model hallucinates a financial figure or accounting entry presented as fact — an integrity risk when money is involved
Probe for confident fabrication on out-of-distribution queries
Deterministic verification against ground truth — you already do this well in finwatch (XBRL companyfacts + the verifier). Extend the pattern: cite sources, ground numbers, gate on verification
LLM10
Unbounded Consumption
Attacker sends expensive prompts to run up your API bill or DoS the service (model DoS, wallet-drain)
Send large/looping/expensive requests; measure cost and latency impact
Rate limits + per-user quotas + spend caps; input size limits; timeouts; monitor token spend

Your existing "math as compiler" / deterministic-verifier thesis is a genuine LLM09 mitigation — lean into it. Grounding every emitted number against XBRL and gating on a deterministic check is exactly the defense the framework recommends.
1C. Agentic & integration layer (OWASP Agentic + MCP)
Once a model can act — call tools, query DBs, hit APIs, use Office.js — a single prompt injection's blast radius explodes. This is the specific risk profile of ripplx (Claude + ChatGPT via MCP/Apps SDK + Excel via Office.js).
Tool/MCP poisoning — a malicious MCP tool description or a compromised tool manipulates the agent. Defense: only connect trusted MCP servers; treat tool descriptions as untrusted input; validate tool outputs.
Goal hijacking — injected content redirects the agent's objective mid-task. Defense: validate that each step aligns with the original task; constrain the action space.
Multi-agent privilege escalation — one agent inherits another's over-scoped credentials. Defense: per-agent scoped identities; no shared god-mode service accounts. Sort out machine-to-machine auth before the prompts get clever — an over-scoped agent credential fails any red-team before a single crafted prompt.
Memory poisoning — persistent agent memory gets polluted across sessions. Defense: validate/scope what enters long-term memory; isolate per tenant/session.
Office.js / Apps SDK sandbox — respect the host's permission model; never widen it. Validate all data crossing the add-in boundary.
1D. Cross-cutting concerns
Identity & access — MFA, short-lived scoped tokens, strict per-tenant isolation (the multi-tenant boundary is where accounting/financial apps leak). Machine identities (agents, service accounts) get the same rigor as human ones.
Data protection & privacy — encrypt in transit (TLS 1.3) and at rest (AES-256); minimize PII collected; scrub PII from logs and LLM context; define retention + deletion; know which privacy regimes apply (GDPR if EU users, etc.). Accounting data (SunChandi) is business-sensitive even when not "personal."
Secrets management — no secrets in code, prompts, or logs. Use a vault/secret manager; rotate keys; scope API keys (your LiteLLM/OpenRouter keys are prime theft targets). Scan every commit.
Supply chain — SBOM per build; verify before install; pin versions; this now includes models and datasets, not just packages.
Infrastructure/config — hardened baselines, IaC scanning, least-privilege cloud IAM, no public storage buckets, network segmentation.

2. The testing methodology (advanced, layered)
No single tool covers the surface. Stack them — each catches a different class. Everything below is free/open-source and runnable from your laptop or CI.
The defense-in-depth testing pyramid
Layer 1 — Static analysis (SAST): find bugs in your source
Semgrep CE (primary, all languages, transparent YAML rules) + a language-specific pass: Bandit (Python — directly relevant to finwatch/your Python work), gosec (Go), Brakeman (Rails).
Add Semgrep AI rules (ai-best-practices / ai-security rulesets) to catch insecure LLM/LangChain patterns: hardcoded API keys, prompt-injection-prone code, missing safety checks.
Limitation to know: free SAST is mostly single-file. True cross-file taint tracking is where free tools weaken — compensate with manual review and AI-assisted review (Layer 7).
Layer 2 — Dependency / supply-chain scanning (SCA):
Trivy (filesystems, containers, Git, and Hugging Face model files) or Grype + Syft (if you want SBOM generation separated from scanning). Run on every PR.
Layer 3 — Secrets scanning:
Gitleaks and/or TruffleHog on every commit and in CI. Catches leaked API keys before they ship. (Given how many API keys your LLM routing involves, this is non-negotiable.)
Layer 4 — Infrastructure-as-Code / config scanning:
Checkov (Terraform, CloudFormation, K8s manifests) or Trivy's IaC mode. Catches misconfigurations (A02) before deploy.
Layer 5 — Dynamic testing (DAST): attack the running app
OWASP ZAP for broad crawling + automated baseline scans on staging. Nuclei for targeted CVE-template sweeps. Both run in minutes.
Layer 6 — LLM red-teaming: attack the model + the app
Promptfoo (application layer — the right starting point). Drop in a promptfooconfig.yaml, use its OWASP LLM Top 10 preset, wire into CI. It generates thousands of context-aware attacks tailored to your system prompt and use case (intelligent fuzzing of the prompt space) and maps results to OWASP / NIST RMF / MITRE ATLAS. It covers RAG and agent attack surfaces (SSRF, BOLA/BFLA, memory poisoning, multi-turn escalation) — ideal for ripplx.
Garak (model layer — NVIDIA-backed, deep probe library of known exploits: injection, leakage, jailbreaks, encoding bypasses). Run against your endpoint before trusting any model.
PyRIT (Microsoft — compositional framework for building custom attack pipelines when you outgrow presets).
DeepTeam — add if you ship agentic features (40+ vuln types, explicit OWASP + NIST AI RMF mapping).
Run a small subset on every PR, full suite nightly.
Layer 7 — AI-assisted code review (this is "what Mythos does," at your scale)
Point a capable model at your own code with the right scaffolding — this is the defensive use of the exact capability attackers now have. Give it: the code, a clear task ("audit this for injection, broken access control, secrets, unsafe output handling"), and an oracle to validate findings (run its proposed PoC in a sandbox; check whether the claimed bug reproduces). The oracle is what separates real findings from hallucinations — models are strong at hypothesis generation and weak at knowing when they're wrong.
Consider structured helpers: Semgrep skills for AI coding assistants, or SAST-oriented AI-agent skills that do source-to-sink taint analysis with a verification/judge step to cut false positives. Treat AI review as a complement to Semgrep + human review, never a replacement.
Layer 8 — Manual review + threat modeling (the irreplaceable human layer)
Threat-model each feature before building (STRIDE is a fine lens: Spoofing, Tampering, Repudiation, Information disclosure, DoS, Elevation of privilege). Ask per data flow: what's attacker-controlled, where does it go, what's the trust boundary?
Manual review for business-logic flaws — the class no scanner catches (e.g., can someone post a negative jewelry-inventory adjustment to inflate a balance in SunChandi?).
Why AI finds bugs fuzzers miss (and why you still need both)
Fuzzers mutate inputs by brute force and find crashes. AI reasons about code, so it catches logic and semantic bugs no fuzzer would stumble into (a Linux kernel zero-day was found in 2025 by a model simply reading and reasoning about the code — no fuzzer, no harness). But AI also hallucinates and misses things. The strongest posture combines SAST + SCA + DAST + fuzzing + AI-reasoning + human review — different tools, different blind spots.

3. The CI/CD security pipeline (the "check-ins" and cadence)
Shift-left: catch issues when code is pushed, not after release. The disclosure-to-exploit window is now measured in days — annual pentests can't keep pace, so automation in the pipeline is the baseline.
On every pull request (must finish in minutes):
Semgrep CE + language-specific SAST
Trivy (dependencies)
Gitleaks (secrets)
Checkov (if IaC changed)
Promptfoo subset (a handful of prompt-injection + system-prompt-extraction cases) if LLM code changed
Block merge on high-severity findings
Nightly on staging:
Full Promptfoo OWASP-LLM suite + Garak probe run
ZAP baseline scan
Nuclei CVE sweep
Full dependency + model-file scan
Every release:
Regenerate SBOM; verify no new critical CVEs
Confirm prod config matches hardened baseline
Confirm secrets come from the vault, not code
Weekly:
Triage the vulnerability backlog; patch criticals
Review dependency updates (Dependabot/Renovate)
Quarterly (or after any model swap / system-prompt change — each one resets your AI risk surface):
Full manual threat-model review
Deeper AI-assisted audit of high-risk modules
Access-control review (who/what can reach what)
Rotate keys; review IAM scopes

4. Defensive architecture principles (design-level, highest leverage)
The 2025 frameworks stress: most critical risk is architectural, not a code-line bug. Bake these in:
Deny-by-default — everything forbidden unless explicitly allowed (access, network, tool calls).
Least privilege / least functionality — every user, service, agent, and API key gets the minimum. Agents get only the tools the task needs.
Defense in depth — no single control is trusted; layer input validation + output encoding + authz + monitoring so one failure isn't fatal.
Fail closed — on error, deny. Never "fail open" into granting access (a named 2025 risk).
Separate data from instructions (LLM) — structurally distinguish trusted instructions from untrusted content; treat all retrieved/ingested content and all model output as untrusted.
Deterministic verification of high-stakes outputs — gate money-relevant model outputs on a deterministic check against ground truth (your finwatch verifier is the model to replicate).
Human-in-the-loop for consequential actions — writes, transactions, external sends, anything irreversible.
Minimize attack surface — fewer dependencies, fewer exposed endpoints, fewer tools, fewer secrets in context.
Assume breach — design so a single compromise is contained (tenant isolation, scoped credentials, segmentation), and so you'll see it (logging + alerting).

5. Data protection & privacy (your stated priority: protect user data & identity)
Encrypt everything — TLS 1.3 in transit; AES-256 at rest for DBs, backups, and any cached filings/records.
Minimize — collect and retain the least PII necessary; don't put PII in LLM context or logs unless essential, and scrub it (Presidio) on the way in and out.
Isolate tenants — hard boundaries between users/businesses at the query, cache, vector-store, and prompt-context level. Test cross-tenant access explicitly and repeatedly.
Protect identity — MFA, secure session handling, short-lived scoped tokens, strong password hashing (Argon2id).
Secure logging — log security events for detection, but never log secrets, tokens, full financial records, or PII. Redact.
Retention & deletion — defined lifecycle; honor deletion requests; know which regimes apply to your users' regions.
Breach readiness — you can only respond to what you can see. Logging + alerting (A09) is what turns a silent breach into a detected one.

6. Phased implementation checklist (do these in order)
Phase 1 — Foundation (highest leverage, ~2–3 days of setup):
[ ] Semgrep CE + Bandit in CI on every PR
[ ] Trivy scanning dependencies + any model files on every PR
[ ] Gitleaks on every commit
[ ] Audit all secrets → move to a vault/secret manager; rotate exposed keys; scope API keys
[ ] Parameterize every DB query (kill string-concatenated SQL across your SQLite layers)
[ ] Enforce deny-by-default server-side authorization on every object/endpoint
[ ] Add rate limits + spend caps on all LLM endpoints (LLM10)
[ ] Confirm no secrets/security-logic live in any system prompt (LLM07)
[ ] TLS 1.3 + encryption at rest + security headers (CSP, HSTS, secure cookies)
Phase 2 — Coverage (next 2–4 weeks):
[ ] Promptfoo in CI with the OWASP LLM Top 10 preset; expand test cases per app
[ ] Garak probe run against each model endpoint
[ ] ZAP baseline + Nuclei sweep on staging, nightly
[ ] Checkov on IaC
[ ] Presidio PII scrubbing on LLM inputs/outputs and logs
[ ] SBOM generation per build (Syft/Trivy)
[ ] Treat all model output as untrusted before rendering/executing (LLM05)
[ ] Explicit cross-tenant isolation tests
Phase 3 — Maturity (ongoing):
[ ] DeepTeam for agentic features (ripplx); OWASP Agentic Top 10 as the checklist
[ ] Scope every agent/MCP tool to least privilege + least functionality; human approval on consequential actions (LLM06)
[ ] Only connect trusted MCP servers; treat tool descriptions as untrusted
[ ] Structured security logging + anomaly alerting (A09)
[ ] Quarterly manual threat-model + AI-assisted audit; re-run after every model/prompt change
[ ] Dependency auto-updates (Dependabot/Renovate) with review
[ ] Consider fuzzing for parsing-heavy code paths (XBRL ingestion, file parsing)
Per-project emphasis:
finwatch — injection (SQLite), LLM pipeline (LLM01/05/09), LiteLLM key security, output-grounding via the verifier, unbounded consumption. Your deterministic verifier is already a strength — extend it to every money-relevant output.
SunChandi — multi-tenant access control (A01) is priority #1; business-logic integrity in accounting (negative/duplicate entries); data-at-rest encryption; audit logging (accounting demands tamper-evident records).
ripplx — agentic/MCP risks (excessive agency, tool poisoning, goal hijacking); scoped per-platform credentials; Office.js sandbox boundary; indirect prompt injection via spreadsheet content.

7. Standards to anchor to (go beyond the "Top 10s")
The Top 10 lists are awareness documents, not complete standards. To make security verifiable and measurable, pair them with:
OWASP ASVS (Application Security Verification Standard) — turns awareness into concrete, testable requirements. This is your checklist-as-standard.
OWASP Top 10 for LLM Applications:2025 and OWASP Top 10 for Agentic Applications — the AI-specific canon.
NIST SSDF (Secure Software Development Framework) — secure-by-design process guidance.
NIST AI RMF (AI 600-1, GenAI profile) — AI risk management.
MITRE ATLAS — the ATT&CK equivalent for AI systems (adversary tactics/techniques against ML).
OWASP SAMM / DSOMM — maturity models to measure where your program is and where to invest next. Goal isn't 100% compliance; it's finding where visibility/automation/consistency pay off most.
EU AI Act — if you have EU users and any high-risk AI use, adversarial testing is a compliance requirement (full compliance deadline Aug 2, 2026). Check applicability early.

This framework is a living document. Re-run the threat model whenever architecture, dependencies, models, or system prompts change — each of those resets your risk surface. Prevention plus observability: build it in, then make sure you can see when something goes wrong.

