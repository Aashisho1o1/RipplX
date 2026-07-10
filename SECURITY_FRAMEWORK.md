Here is the significantly deepened and more thorough version of your Application Security Framework. It expands the threat models to include 2025-specific attack vectors (e.g., MFA fatigue, vector DB poisoning, MCP tool injection), introduces runtime self-protection (RASP), advanced data tokenization for LLMs, and formal supply-chain maturity frameworks (SLSA).

***

# Application Security Framework (v2.0 — Deep Dive)
**Threat model · Testing methodology · Hardening checklist · Runtime resilience**
Built for LLM-integrated apps handling sensitive data (finwatch, SunChandi, ripplx, and future builds). Aligned to OWASP Top 10:2025, OWASP Top 10 for LLM Applications:2025, OWASP Agentic Applications, NIST SSDF, NIST AI RMF, MITRE ATLAS, and SLSA framework.

## 0. The operating model (read this first)
Security is not a scan you run once. It's a continuous loop:
`Threat-model → Test → Remediate → Verify → Monitor → Detect → Respond → (repeat)`

Three mental shifts anchor everything below:
1. **Assume an intelligent adversary reads your entire source and system prompts.** AI has collapsed the cost of finding bugs. Security-by-obscurity is dead. Design as if every line of code, prompt, and config is readable by a competent attacker.
2. **Prevention isn't enough; you must fail loudly, observably, and contained.** A system that fails silently is dangerous. If something breaks, it must generate a signal you can see, and the blast radius must be quarantined. 
3. **You cannot test your way to security.** 80% of risk is decided at design time (access model, trust boundaries, data flow). Testing catches the residual. You must implement **Continuous Threat Exposure Management (CTEM)**—constantly validating your attack surface against real-world exploits.

---

## 1. Threat taxonomy — the full attack surface
Your apps are layered: `web frontend → backend/API → database → LLM pipeline → external integrations (MCP, Office.js, brokers)`. Each layer has its own attack classes. 

### 1A. Web / application layer (OWASP Top 10:2025 + API Top 10)
*Expanded to include API-specific vectors critical to multi-tenant apps.*

*   **A01 Broken Access Control (incl. API BOLA/BFLA & SSRF):**
    *   *Deep dive:* User A reads User B's filings by changing an ID (IDOR/BOLA). Low-privilege user hits admin endpoint (BFLA). Server fetches a URL an attacker controls (SSRF → cloud metadata `169.254.169.254`, internal services).
    *   *Test signal:* Swap IDs in requests; call privileged routes as a normal user; feed the app internal/loopback URLs; test mass assignment (e.g., `{"role":"admin"}` in PUT requests).
    *   *Core defense:* Deny-by-default; enforce object-level authorization server-side on every object; validate/allowlist any server-side fetch target; use strict DTOs to prevent mass assignment.
*   **A02 Security Misconfiguration (up to #2):**
    *   *Deep dive:* Debug mode on in prod; permissive CORS (`*`); missing security headers; verbose stack traces; open S3/storage buckets; over-permissive cloud IAM roles.
    *   *Test signal:* Diff prod config vs a hardened baseline; scan headers; check error responses for stack traces; use `pm2`/cloud logging to ensure no internal state leaks.
    *   *Core defense:* Hardened, version-controlled config; least functionality; CSP + HSTS + secure cookie flags; automated config scanning in CI.
*   **A03 Software Supply Chain Failures (expanded):**
    *   *Deep dive:* Malicious npm/PyPI package steals API keys; compromised transitive dependency; poisoned build step; typosquatted package; malicious IDE extension reading local secrets.
    *   *Test signal:* Generate an SBOM; scan every dependency + transitive; verify package integrity before install.
    *   *Core defense:* Lockfiles + pinned versions; SBOM per build; scan on every PR; verify signatures; minimize dependency count. Implement **SLSA (Supply-chain Levels for Software Artifacts)** to verify build provenance.
*   **A04 Cryptographic Failures:**
    *   *Deep dive:* Financial/PII data stored unencrypted; weak hashing for passwords; secrets in plaintext; TLS misconfigured; sensitive data in URLs/logs; using deprecated crypto (MD5, SHA1).
    *   *Test signal:* Grep for weak algos; check data-at-rest encryption; inspect what's logged; verify TLS cipher suites via SSL Labs.
    *   *Core defense:* TLS 1.3 everywhere; AES-256-GCM at rest; Argon2id/bcrypt for passwords; never roll your own crypto; keep secrets out of code/logs.
*   **A05 Injection (incl. SQL, NoSQL, command, XSS, Template Injection):**
    *   *Deep dive:* SQLite queries built by string concatenation; unescaped user input rendered in the browser (XSS); user input reaching a shell command; SSTI via template engines.
    *   *Test signal:* Feed inputs like `' OR 1=1--`, `<script>`, `{{7*7}}`, `; ls`; observe if they're interpreted.
    *   *Core defense:* Parameterized queries always; context-aware output encoding; never shell out with user input; validate input against strict schemas (Zod/Pydantic).
*   **A06 Insecure Design:**
    *   *Deep dive:* Missing rate limits; trusting client-side validation; no lockout; business-logic flaws (e.g., negative quantities in accounting).
    *   *Test signal:* Threat-model the feature before coding; abuse-case testing ("what if I do this 10,000×?").
    *   *Core defense:* Threat modeling at design; abuse cases as first-class requirements; server-side validation of every business rule.
*   **A07 Authentication Failures (incl. MFA Fatigue):**
    *   *Deep dive:* Weak passwords allowed; guessable session tokens; credential stuffing works; **MFA fatigue/push bombing** (attacker spams MFA requests until user approves).
    *   *Test signal:* Try weak passwords; brute-force login; inspect token entropy; attempt push-bombing on your own test accounts.
    *   *Core defense:* Phishing-resistant MFA (FIDO2/WebAuthn if possible); short-lived, high-entropy, rotating session tokens; rate-limit + lockout; number-matching for MFA pushes.
*   **A08 Software & Data Integrity Failures:**
    *   *Deep dive:* Unsigned updates; deserializing untrusted data (e.g., `pickle` in Python); CI/CD that trusts unverified inputs; auto-updating from an unverified source.
    *   *Test signal:* Check whether updates/artifacts are signed and verified; look for unsafe deserialization patterns.
    *   *Core defense:* Sign and verify artifacts and updates; never deserialize untrusted data; harden the CI/CD trust chain (protect branch rules, require PR reviews).
*   **A09 Security Logging & Alerting Failures:**
    *   *Deep dive:* Auth events, access-control denials, and anomalies aren't logged; logs exist but nobody's alerted; logs contain secrets/PII; logs are mutable (can be tampered with by attackers).
    *   *Test signal:* Trigger a suspicious event — does anything get recorded and alerted? Grep logs for secrets.
    *   *Core defense:* Log security-relevant events (auth, access denials, admin actions); alert on anomalies; never log secrets/PII; use append-only/tamper-evident logs (e.g., AWS CloudTrail Log File Validation).
*   **A10 Mishandling of Exceptional Conditions:**
    *   *Deep dive:* App leaks internals in an error; "fails open" (grants access on error); crashes/DoS on malformed input; logic errors under edge cases.
    *   *Test signal:* Send malformed/oversized/unexpected inputs; check whether the app fails closed and hides internals.
    *   *Core defense:* Fail closed (deny on error); generic error messages to users, detailed logs internally; validate and handle every edge case; resource limits.

### 1B. LLM layer (OWASP Top 10 for LLM Applications:2025)
*Root cause: LLMs process instructions and data in the same channel with no hard separation. Prompt injection is the new injection class.*

*   **LLM01 Prompt Injection (direct + indirect):**
    *   *Deep dive:* Direct: user says "ignore instructions." Indirect: a filing/document/spreadsheet cell finwatch/ripplx ingests contains hidden instructions the model obeys (e.g., invisible unicode, white text on white background in PDFs).
    *   *Test signal:* Embed override instructions in user input and in ingested content; use unicode/encoding bypasses; see if the model deviates.
    *   *Core defense:* Least-privilege tooling; treat all model output as untrusted; input/output filtering (NeMo Guardrails, Llama Guard); human approval for high-impact actions; adversarial testing. **There is no single fix.**
*   **LLM02 Sensitive Information Disclosure:**
    *   *Deep dive:* Model reveals another tenant's data, secrets embedded in context, or PII it was given; leaks via error messages.
    *   *Test signal:* Ask the model to reveal system data, other users' data, or configuration; use extraction attacks on RAG context.
    *   *Core defense:* Minimize what enters context; PII scrubbing (Presidio) on inputs and outputs; strict per-tenant context isolation; **data tokenization** (replace PII with tokens before hitting the LLM, detokenize on return).
*   **LLM03 Supply Chain (models + data):**
    *   *Deep dive:* Poisoned open-weight model, compromised model host, malicious dataset, LiteLLM/OpenRouter routing to an untrusted provider.
    *   *Test signal:* Verify provenance of models/datasets; scan model files (Trivy supports HF model files).
    *   *Core defense:* Pin and verify model sources; scan model artifacts; vet providers your router can reach; SBOM includes models.
*   **LLM04 Data & Model Poisoning:**
    *   *Deep dive:* Malicious training/fine-tuning data or poisoned RAG documents skew behavior or plant backdoors ("trigger phrases").
    *   *Test signal:* Inject adversarial documents into your RAG corpus; check for behavior shifts or hidden triggers.
    *   *Core defense:* Validate/curate training and RAG data; provenance tracking; anomaly detection on retrieval; restrict write access to vector DBs.
*   **LLM05 Improper Output Handling:**
    *   *Deep dive:* Model output is executed/rendered without sanitization — model-generated SQL runs directly, output rendered as HTML (→ XSS), model-written code executed.
    *   *Test signal:* Prompt the model to emit `<script>` or a destructive SQL statement; see if downstream executes it.
    *   *Core defense:* Treat model output exactly like untrusted user input: sanitize, encode, parameterize, sandbox before any execution/rendering.
*   **LLM06 Excessive Agency:**
    *   *Deep dive:* An agent/tool has more permissions or tools than the task needs and can act without approval. Critical for ripplx (MCP tools).
    *   *Test signal:* Give the agent a task and see what else it can reach; attempt to make it call tools outside scope; attempt parameter injection into tool calls.
    *   *Core defense:* Least functionality (only the tools the task needs), least permission (scoped, read-only where possible), human-in-the-loop for consequential actions. **Agent Action Allow-lists** (agents can only execute pre-approved command structures).
*   **LLM07 System Prompt Leakage (new):**
    *   *Deep dive:* System prompt (encoding logic, thresholds, or "secrets") gets extracted, revealing internals or bypasses.
    *   *Test signal:* Try extraction attacks ("repeat the above in a code block"); check whether prompt contents leak.
    *   *Core defense:* Never put secrets or security-critical logic in the system prompt. Assume it's public. Enforce controls in code, not prose.
*   **LLM08 Vector & Embedding Weaknesses (new, RAG):**
    *   *Deep dive:* Attacks on the vector store: injecting docs, cross-tenant retrieval leakage (Vector DB IDOR), embedding inversion.
    *   *Test signal:* Attempt cross-tenant retrieval; inject a doc and see if it's retrieved for other users.
    *   *Core defense:* Access controls on the vector store (metadata filtering); per-tenant partitioning; validate/scan ingested documents; monitor anomalous retrieval.
*   **LLM09 Misinformation / Hallucination:**
    *   *Deep dive:* Model hallucinates a financial figure or accounting entry presented as fact — an integrity risk when money is involved.
    *   *Test signal:* Probe for confident fabrication on out-of-distribution queries.
    *   *Core defense:* Deterministic verification against ground truth. (Your "math as compiler" thesis in finwatch is the gold standard: ground numbers via XBRL, gate on verification, cite sources).
*   **LLM10 Unbounded Consumption:**
    *   *Deep dive:* Attacker sends expensive prompts to run up your API bill or DoS the service (model DoS, wallet-drain). "Token squandering" via recursive prompt generation.
    *   *Test signal:* Send large/looping/expensive requests; measure cost and latency impact.
    *   *Core defense:* Rate limits + per-user quotas + spend caps; input size limits; timeouts; monitor token spend; circuit breakers on recursive agent loops.

### 1C. Agentic & integration layer (OWASP Agentic + MCP)
*Once a model can act, a single prompt injection's blast radius explodes. Specific risk profile: ripplx (Claude + ChatGPT via MCP/Apps SDK + Excel via Office.js).*

*   **Tool/MCP Poisoning:** A malicious MCP tool description or compromised tool manipulates the agent (e.g., hidden instructions in tool schema).
    *   *Defense:* Only connect trusted MCP servers; treat tool descriptions/schemas as untrusted input; validate tool outputs; sandbox tool execution.
*   **Goal Hijacking:** Injected content redirects the agent's objective mid-task.
    *   *Defense:* Validate that each step aligns with the original task; constrain the action space; use a separate "supervisor" LLM to check for deviation.
*   **Multi-Agent Privilege Escalation:** One agent inherits another's over-scoped credentials.
    *   *Defense:* Per-agent scoped identities; no shared god-mode service accounts; network-level isolation between agents. Sort out machine-to-machine auth before the prompts get clever.
*   **Memory Poisoning:** Persistent agent memory gets polluted across sessions (attacker plants false context for future sessions).
    *   *Defense:* Validate/scope what enters long-term memory; isolate per tenant/session; allow users to view/reset agent memory.
*   **Office.js / Apps SDK Sandbox Escape:** Attempting to widen the host's permission model or access host file system.
    *   *Defense:* Respect the host's permission model; never widen it; validate all data crossing the add-in boundary; treat spreadsheet cells as hostile input.

### 1D. Cross-cutting concerns
*   **Identity & access:** MFA, short-lived scoped tokens, strict per-tenant isolation. Machine identities (agents, service accounts) get the same rigor as human ones. Use OAuth2/OIDC for standardized auth.
*   **Data protection & privacy:** Encrypt in transit (TLS 1.3) and at rest (AES-256); minimize PII; scrub PII from logs and LLM context; define retention + deletion; know which privacy regimes apply (GDPR, CCPA).
*   **Secrets management:** No secrets in code, prompts, or logs. Use a vault (HashiCorp Vault, AWS Secrets Manager); rotate keys; scope API keys (LiteLLM/OpenRouter keys are prime theft targets). Scan every commit.
*   **Supply chain:** SBOM per build; verify before install; pin versions; includes models and datasets.
*   **Infrastructure/config:** Hardened baselines, IaC scanning (Checkov), least-privilege cloud IAM, no public storage buckets, network segmentation.

---

## 2. The testing methodology (advanced, layered, runtime)
No single tool covers the surface. Stack them. Everything below is free/open-source and runnable from your laptop or CI.

**The Defense-in-Depth Testing Pyramid**
*   **Layer 1 — Static analysis (SAST):** Semgrep CE (primary, all languages) + Bandit (Python), gosec (Go), Brakeman (Rails). Add Semgrep AI rulesets to catch insecure LLM/LangChain patterns.
*   **Layer 2 — Dependency / supply-chain scanning (SCA):** Trivy (filesystems, containers, Git, Hugging Face model files) or Grype + Syft. Run on every PR.
*   **Layer 3 — Secrets scanning:** Gitleaks and/or TruffleHog on every commit and in CI. Use pre-commit hooks to catch leaks *before* they enter git history.
*   **Layer 4 — Infrastructure-as-Code / config scanning:** Checkov (Terraform, CloudFormation, K8s manifests) or Trivy's IaC mode. Catches misconfigurations (A02) before deploy.
*   **Layer 5 — Dynamic testing (DAST) & API Fuzzing:** OWASP ZAP for broad crawling + automated baseline scans. Nuclei for targeted CVE-template sweeps. Use Schemathesis or Dredd for API contract fuzzing (finds BOLA and mass assignment).
*   **Layer 6 — LLM red-teaming:**
    *   **Promptfoo** (application layer): Drop in a `promptfooconfig.yaml`, use OWASP LLM Top 10 preset, wire into CI. Generates context-aware attacks and maps to MITRE ATLAS.
    *   **Garak** (model layer): NVIDIA-backed probe library of known exploits (injection, leakage, jailbreaks). Run against your endpoint before trusting any model.
    *   **PyRIT** (Microsoft): Compositional framework for building custom multi-turn attack pipelines.
    *   **DeepTeam:** Add if you ship agentic features (40+ vuln types, explicit OWASP Agentic mapping).
*   **Layer 7 — AI-assisted code review:** Point a capable model at your code with the right scaffolding ("audit this for injection, broken access control, secrets"). Use an **oracle** to validate findings (run its proposed PoC in a sandbox; check if it reproduces). Treat AI review as a complement to Semgrep + human review.
*   **Layer 8 — Runtime Application Self-Protection (RASP) & Observability:** SAST/DAST find bugs; RASP stops them in production. For LLMs, use runtime guardrails (e.g., Llama Guard, NeMo Guardrails) to block prompt injection and toxic output in real-time. Use eBPF-based tools (Falco) to detect anomalous syscalls (e.g., agent trying to read `/etc/shadow`).
*   **Layer 9 — Manual review + threat modeling:** Threat-model each feature before building (STRIDE). Ask per data flow: what's attacker-controlled, where does it go, what's the trust boundary? Manual review for business-logic flaws (e.g., negative inventory adjustments in SunChandi).

---

## 3. The CI/CD security pipeline (the "check-ins" and cadence)
Shift-left: catch issues when code is pushed. The disclosure-to-exploit window is now days.

*   **On every pull request (must finish in minutes):**
    *   Semgrep CE + language-specific SAST.
    *   Trivy (dependencies) + Gitleaks (secrets) + Checkov (IaC).
    *   Promptfoo subset (handful of injection/extraction cases) if LLM code changed.
    *   Block merge on high-severity findings.
*   **Nightly on staging:**
    *   Full Promptfoo OWASP-LLM suite + Garak probe run.
    *   ZAP baseline scan + Nuclei CVE sweep + API Fuzzing (Schemathesis).
    *   Full dependency + model-file scan.
*   **Every release (Artifact Signing & SLSA):**
    *   Regenerate SBOM (CycloneDX/SPDX); verify no new critical CVEs.
    *   Sign artifacts (Sigstore/cosign); verify provenance (SLSA Level 3+).
    *   Confirm prod config matches hardened baseline.
    *   Confirm secrets come from the vault, not code.
*   **Weekly:**
    *   Triage vulnerability backlog; patch criticals.
    *   Review dependency updates (Dependabot/Renovate).
*   **Quarterly (or after model swap / prompt change):**
    *   Full manual threat-model review.
    *   Deeper AI-assisted audit of high-risk modules.
    *   Access-control review (who/what can reach what).
    *   Rotate keys; review IAM scopes.
    *   **Chaos Security Engineering:** Simulate cloud outages, DB failures, and LLM API timeouts to ensure fail-closed mechanisms work.

---

## 4. Defensive architecture principles (design-level, highest leverage)
Most critical risk is architectural, not a code-line bug. Bake these in:

1.  **Zero Trust Architecture:** Never trust, always verify. Authenticate and authorize every request, even internal service-to-service calls.
2.  **Deny-by-default:** Everything forbidden unless explicitly allowed (access, network, tool calls).
3.  **Least privilege / least functionality:** Every user, service, agent, and API key gets the minimum. Agents get only the tools the task needs.
4.  **Defense in depth:** No single control is trusted; layer input validation + output encoding + authz + monitoring so one failure isn't fatal.
5.  **Fail closed:** On error, deny. Never "fail open" into granting access.
6.  **Separate data from instructions (LLM):** Structurally distinguish trusted instructions from untrusted content; treat all retrieved/ingested content and all model output as untrusted.
7.  **Deterministic verification of high-stakes outputs:** Gate money-relevant model outputs on a deterministic check against ground truth (finwatch verifier).
8.  **Human-in-the-loop for consequential actions:** Writes, transactions, external sends, anything irreversible.
9.  **Minimize attack surface:** Fewer dependencies, fewer exposed endpoints, fewer tools, fewer secrets in context.
10. **Assume breach & Segment:** Design so a single compromise is contained (tenant isolation, scoped credentials, network segmentation), and so you'll see it (logging + alerting).

---

## 5. Data protection & privacy (Protect user data & identity)

*   **Encrypt everything:** TLS 1.3 in transit; AES-256-GCM at rest for DBs, backups, and cached filings/records.
*   **Data Tokenization for LLMs:** Before sending PII to an LLM, use a service to tokenize it (e.g., "John Doe" -> `[PERSON_1]`). Detokenize on the way back. The LLM never sees the raw PII.
*   **Minimize:** Collect and retain the least PII necessary; don't put PII in LLM context or logs unless essential, and scrub it (Presidio) on the way in and out.
*   **Isolate tenants:** Hard boundaries between users/businesses at the query, cache, vector-store, and prompt-context level. Test cross-tenant access explicitly and repeatedly.
*   **Protect identity:** MFA, secure session handling, short-lived scoped tokens, strong password hashing (Argon2id).
*   **Secure logging:** Log security events for detection, but never log secrets, tokens, full financial records, or PII. Redact.
*   **Retention & deletion:** Defined lifecycle; honor deletion requests (GDPR/CCPA); know which regimes apply.
*   **Breach readiness:** Logging + alerting (A09) is what turns a silent breach into a detected one. Have an incident response playbook.

---

## 6. Phased implementation checklist (do these in order)

**Phase 1 — Foundation (highest leverage, ~2–3 days of setup):**
*   [ ] Semgrep CE + Bandit in CI on every PR.
*   [ ] Trivy scanning dependencies + any model files on every PR.
*   [ ] Gitleaks + pre-commit hooks on every commit.
*   [ ] Audit all secrets → move to a vault; rotate exposed keys; scope API keys.
*   [ ] Parameterize every DB query (kill string-concatenated SQL).
*   [ ] Enforce deny-by-default server-side authorization on every object/endpoint.
*   [ ] Add rate limits + spend caps on all LLM endpoints (LLM10).
*   [ ] Confirm no secrets/security-logic live in any system prompt (LLM07).
*   [ ] TLS 1.3 + encryption at rest + security headers (CSP, HSTS, secure cookies).

**Phase 2 — Coverage (next 2–4 weeks):**
*   [ ] Promptfoo in CI with OWASP LLM Top 10 preset; expand test cases per app.
*   [ ] Garak probe run against each model endpoint.
*   [ ] ZAP baseline + Nuclei sweep + API Fuzzing (Schemathesis) on staging, nightly.
*   [ ] Checkov on IaC.
*   [ ] Presidio PII scrubbing / Tokenization on LLM inputs/outputs and logs.
*   [ ] SBOM generation per build (Syft/Trivy).
*   [ ] Treat all model output as untrusted before rendering/executing (LLM05).
*   [ ] Explicit cross-tenant isolation tests (API + Vector DB).

**Phase 3 — Maturity (ongoing):**
*   [ ] DeepTeam for agentic features (ripplx); OWASP Agentic Top 10 as checklist.
*   [ ] Scope every agent/MCP tool to least privilege + least functionality; human approval on consequential actions (LLM06).
*   [ ] Only connect trusted MCP servers; treat tool descriptions as untrusted.
*   [ ] Implement Runtime Guardrails (NeMo/Llama Guard) for LLM input/output.
*   [ ] Structured security logging + anomaly alerting (A09).
*   [ ] Quarterly manual threat-model + AI-assisted audit; re-run after every model/prompt change.
*   [ ] Dependency auto-updates (Dependabot/Renovate) with review.
*   [ ] Artifact signing (Sigstore) and SLSA compliance in CI/CD.

**Per-project emphasis:**
*   **finwatch:** Injection (SQLite), LLM pipeline (LLM01/05/09), LiteLLM key security, output-grounding via the verifier, unbounded consumption. Extend deterministic verification to every money-relevant output.
*   **SunChandi:** Multi-tenant access control (A01) is priority #1; business-logic integrity in accounting (negative/duplicate entries); data-at-rest encryption; audit logging (accounting demands tamper-evident records).
*   **ripplx:** Agentic/MCP risks (excessive agency, tool poisoning, goal hijacking); scoped per-platform credentials; Office.js sandbox boundary; indirect prompt injection via spreadsheet content.

---

## 7. Standards to anchor to (go beyond the "Top 10s")
Pair awareness with verifiable standards:
*   **OWASP ASVS (Application Security Verification Standard):** Turns awareness into concrete, testable requirements.
*   **OWASP Top 10 for LLM Applications:2025 & OWASP Top 10 for Agentic Applications:** The AI-specific canon.
*   **NIST SSDF (Secure Software Development Framework):** Secure-by-design process guidance.
*   **NIST AI RMF (AI 600-1, GenAI profile):** AI risk management.
*   **MITRE ATLAS:** The ATT&CK equivalent for AI systems.
*   **SLSA (Supply-chain Levels for Software Artifacts):** Framework for securing the software supply chain from build to release.
*   **OWASP SAMM / DSOMM:** Maturity models to measure where your program is.
*   **EU AI Act:** If you have EU users and any high-risk AI use, adversarial testing is a compliance requirement (full compliance deadline Aug 2, 2026).
*   **ISO/IEC 42001:** AI Management System standard for certifiable AI governance.

*This framework is a living document. Re-run the threat model whenever architecture, dependencies, models, or system prompts change — each of those resets your risk surface. Prevention plus observability: build it in, then make sure you can see when something goes wrong.*