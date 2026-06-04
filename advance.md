# Ariadne — Data Lineage Platform
## Business & Delivery Document

| | |
|---|---|
| **Document type** | Solution & Delivery Assessment |
| **Prepared for** | Solution Architect · Delivery Manager |
| **Product** | Ariadne — Multi-Parser Data Lineage Platform |
| **Status** | Working build; four parsers functional; platform integrated end-to-end |
| **Date** | 2026-06-04 |
| **Classification** | Internal — not for redistribution |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Business Problem & Opportunity](#2-business-problem--opportunity)
3. [Solution Overview](#3-solution-overview)
4. [Business Value & Use Cases](#4-business-value--use-cases)
5. [Scope — In and Out](#5-scope--in-and-out)
6. [Architecture Summary (for the Architect)](#6-architecture-summary)
7. [Key Architecture Decisions & Rationale](#7-key-architecture-decisions--rationale)
8. [Delivery Status (for the Delivery Manager)](#8-delivery-status)
9. [Quality & Test Evidence](#9-quality--test-evidence)
10. [Risks, Limitations & Mitigations](#10-risks-limitations--mitigations)
11. [Non-Functional Posture](#11-non-functional-posture)
12. [Cost & Deployment](#12-cost--deployment)
13. [Roadmap & Recommendations](#13-roadmap--recommendations)
14. [Decisions Requested](#14-decisions-requested)
15. [Appendix — Glossary](#15-appendix--glossary)

---

## 1. Executive Summary

**The problem.** Enterprise data flows through many disconnected tools — Tableau dashboards,
QlikView load scripts, IBM Tivoli (TWS) job schedules, and Spark/PySpark pipelines. No single
system can answer basic governance questions such as *"if this table changes, which dashboards
break?"* or *"where did this number come from?"* Today those answers require manual investigation
across multiple teams, which is slow, error-prone, and expensive.

**The solution.** Ariadne reads the native files from all four tools and automatically stitches
them into a **single connected lineage map** (a Neo4j knowledge graph, with a Postgres mirror for
SQL-style queries). A web application lets users trace any column, table, dashboard, or job
backward to its source or forward to its impact — across tool boundaries — with no manual mapping.

**Why it works.** Every parser converts physical identifiers (table names, file paths, S3 URIs)
into the **same deterministic fingerprint (SHA-256)**. Independent parsers therefore write to the
same graph node automatically — end-to-end lineage with **no glue code, no ETL, and no central
mapping table to maintain.**

**Where we are.** All four parsers are built and functional, the platform is integrated end-to-end
(parsers → graph/SQL → gateway → web UI), and the solution is backed by approximately **800
automated tests**. The Spark parser has additionally been validated against **97 real-world files
with a 99% success rate**. The build runs locally via Docker and has a one-instance AWS deployment
path.

**What we need.** This document asks the architect and delivery manager to (a) confirm the
architecture direction, (b) accept the current scope boundaries, and (c) prioritise the
hardening items required before any production/enterprise rollout — chiefly **authentication and
access control**, which is intentionally not yet in place.

---

## 2. Business Problem & Opportunity

### The pain today

| Question the business needs answered | How it's answered today | Cost |
|---|---|---|
| "If we change/retire this table, what breaks downstream?" | Manual code review across teams | Days of effort; high risk of missed impact |
| "Where did the figure on this dashboard actually come from?" | Tribal knowledge, tracing by hand | Hours per request; not repeatable |
| "Which scheduled jobs touch this table, and when do they run?" | Reading TWS dumps manually | Slow; error-prone |
| "Are we compliant — can we prove data lineage to auditors?" | No systematic evidence | Audit risk |

### Why it matters

- **Change risk.** Without impact analysis, routine changes cause unplanned outages.
- **Audit & compliance.** Regulators increasingly expect demonstrable data lineage.
- **Operational efficiency.** Analysts and engineers spend significant time on manual tracing.
- **Onboarding.** New team members cannot see how the estate fits together.

### The opportunity

A single, automated, cross-tool lineage map turns multi-day investigations into **point-and-click
traces**, reduces change-related incidents, and produces audit-ready lineage evidence as a
by-product.

---

## 3. Solution Overview

Ariadne is composed of **four independent parsers** feeding a **shared knowledge graph**, exposed
through **one API gateway** and a **web explorer**.

```
   Source files            Parsers (independent)        Shared storage          User access
  --------------          ----------------------       ----------------        -------------
  Tableau .twb/.twbx ──►   Tableau parser  :8001 ─┐
  TWS dumps          ──►   TWS parser      :8002 ─┤    Neo4j (graph)
  QlikView .qvs/.qvw ──►   QlikView parser :8003 ─┼──► + ──────────────► Gateway :8000 ──► Web UI :3000
  Spark .py/.sql     ──►   Spark parser    :8004 ─┘    Postgres (SQL mirror)
```

**Design principle:** the parsers never talk to each other, yet their outputs merge automatically
because they share one deterministic identity scheme. This keeps the parsers decoupled (independent
build, test, and failure domains) while still producing one connected result.

---

## 4. Business Value & Use Cases

| Use case | Who benefits | Value delivered |
|---|---|---|
| **Impact analysis** ("what breaks if this changes?") | Engineering, Change Management | Prevents outages; faster, safer releases |
| **Root-cause / provenance** ("where did this number come from?") | Analysts, Data Governance | Trust in reporting; faster issue resolution |
| **Schedule intelligence** ("what runs 05:30–06:30 and touches table X?") | Operations / Run teams | Faster incident response; batch optimisation |
| **Audit evidence** (end-to-end lineage on demand) | Compliance, Risk | Audit-ready, repeatable lineage |
| **Onboarding & documentation** (live map of the estate) | New joiners, Architecture | Reduced ramp-up time |

**Signature capability — cross-tool tracing.** A single query can follow a Tableau dashboard down
to the physical table it depends on and then to the Spark job that produces that table. This is the
differentiator versus single-tool lineage products, and it is achieved with no manual stitching.

---

## 5. Scope — In and Out

### In scope (delivered)

- Parsing of **Tableau** (`.twb`, `.twbx`), **QlikView** (`.qvs`, `.qvw`, `.qvf`), **TWS**
  (composer-text and XML), and **Spark** (`.py`, `.sql`, `.ipynb`, `.dbc`).
- Automatic cross-tool stitching via deterministic IDs.
- Column-level lineage where the source supports it (notably Spark and Tableau calculated fields).
- Web UI: dashboard/health, graph explorer, lineage tracer, TWS operational search, file upload.
- Dual storage: Neo4j (graph traversal) + Postgres (SQL-style operational queries for TWS).
- Local Docker deployment and a single-instance AWS deployment path.

### Out of scope (current build) — deliberate boundaries

- **Authentication / authorisation / multi-tenancy** — assumes a trusted network today.
- **Live runtime history** — we parse *definitions* (code, schedules), not actual run logs.
- **Scala Spark** — not parsed (PySpark and Spark SQL only).
- **Reading inside extract/binary data files** — Tableau `.hyper`/`.tde` are flagged, not introspected; UDF bodies are not opened.
- **Column-level lineage inside hand-written custom SQL** in Tableau (tables are captured; per-column flow is not yet).

These boundaries are reasonable for the current stage but **must be reviewed before enterprise
rollout** — see [Section 10](#10-risks-limitations--mitigations) and [Section 14](#14-decisions-requested).

---

## 6. Architecture Summary

### Components

| Component | Technology | Responsibility |
|---|---|---|
| Parsers (×4) | Python 3.11, FastAPI; ANTLR4 (QlikView, TWS), `sqlglot` (SQL), Python `ast` (Spark), `lxml` (Tableau/TWS XML) | Read source files → write lineage to storage |
| Graph store | Neo4j 5.20 (community + APOC) | Master lineage map; traversal queries |
| SQL mirror | Postgres 16 | Operational TWS queries (time windows, filters) |
| Gateway | FastAPI | Single entry point; routing; read-only query guard; canned lineage presets |
| Frontend | Next.js 14, TypeScript, IBM Carbon, Cytoscape.js | Web explorer and tracer |
| Orchestration | Docker Compose (dev); Terraform single-EC2 (cloud) | Run and deploy the stack |

### The stitching mechanism (the core of the design)

1. Each parser builds a canonical string for a physical object, e.g. `prod.sales.orders`.
2. It hashes that string with **SHA-256** to produce a stable node ID.
3. It writes using Neo4j `MERGE` (create-if-absent).
4. Because every parser uses the identical recipe, two parsers referencing the same object land on
   the **same node** — lineage threads connect automatically.

### Determinism guarantees

- IDs depend only on the input — no clocks, no randomness (`PYTHONHASHSEED=0` enforced).
- Re-parsing the same file is a no-op on the graph (idempotent).
- These guarantees are protected by dedicated tests; they are not incidental — they are what makes
  cross-tool merging correct.

---

## 7. Key Architecture Decisions & Rationale

| Decision | Rationale | Trade-off / note for the Architect |
|---|---|---|
| **Four independent parser services** | Isolated build/test/failure domains; per-source ownership | More services to operate; mitigated by shared contracts |
| **Deterministic SHA-256 identity** | Enables zero-glue cross-tool merge; reproducible & idempotent | Identity recipe is a hard contract — any change breaks historical merges; must be version-controlled |
| **Neo4j as master + Postgres mirror** | Graph fits traversal/impact analysis; SQL fits operational filters | Dual writes are **not** a single transaction (eventual consistency between stores) |
| **Static analysis (no execution)** | Safe, fast, no clusters/credentials needed to parse Spark | Cannot resolve fully dynamic/runtime-built names — handled via partial-lineage markers |
| **ANTLR grammars for QlikView/TWS** | Robust, maintainable parsing of complex DSLs vs. fragile regex | Requires a grammar-regeneration build step (developer discipline) |
| **Graceful degradation over guessing** | A flagged partial answer is safer than a confident wrong one | Consumers must surface `lineage_partial` markers to users |
| **Read-only query guard at the gateway** | Prevents accidental/malicious graph mutation via the API | Not a substitute for authentication (see risks) |

**Architecturally significant item to ratify:** the **identity contract** (`lineage-contracts`).
It is the linchpin of the platform. Recommendation: treat it as a versioned, governed interface
with explicit change control.

---

## 8. Delivery Status

### Overall status: **Working, integrated build; pre-production**

| Workstream | Status | Notes |
|---|---|---|
| Tableau parser | ✅ Functional | ~173 tests; 12 sample workbooks |
| QlikView parser | ✅ Functional | ~193 tests; CI gates for ID stability, idempotency, secret-leak |
| TWS parser | ✅ Functional | ~152 tests; dual Neo4j + Postgres write |
| Spark parser | ✅ Functional | ~238 tests; validated on 97 real-world files (99% pass) |
| Gateway | ✅ Functional | ~42 tests; routing + read-only guard |
| Frontend | ✅ Functional | Dashboard, Explorer, Tracer, TWS ops, Upload |
| Local deployment (Docker) | ✅ Working | `start.sh` / `refresh.sh` / `stop.sh` |
| Cloud deployment (AWS) | ✅ Path exists | Single-EC2 Terraform module |
| **Authentication / RBAC** | ❌ Not started | **Required before production** |
| **Enterprise-scale performance test** | ⚠️ Partial | Spark validated on real corpus; others on hand-built fixtures |

### Readiness assessment

- **Demo / pilot ready:** Yes — the platform runs end-to-end and demonstrates cross-tool lineage today.
- **Production ready:** No — pending authentication, scale testing on production-volume estates, and
  a decision on store-consistency handling.

---

## 9. Quality & Test Evidence

### Test coverage at a glance

| Area | Approx. test count | Sample inputs |
|---|---:|---|
| Tableau parser | ~173 | 12 workbooks |
| QlikView parser | ~193 | 10 scripts |
| TWS parser | ~152 | 8 dumps (text + XML) |
| Spark parser | ~238 | 30+ scripts/notebooks |
| Gateway | ~42 | mocked stores |
| **Total** | **~800 automated tests** | |

### Types of assurance in place

- **Unit + integration tests** across every parser and the gateway.
- **Determinism tests** — same input produces byte-identical IDs across repeated runs.
- **Idempotency tests** — re-parsing creates no duplicates.
- **Cross-parser merge tests** — two parsers referencing the same object land on one node.
- **Security tests** — confirm credentials/passwords are scrubbed and never persisted.
- **Golden-output tests** (Spark) — output compared exactly against hand-verified expected results.
- **Real-world validation** (Spark) — **96 of 97** public PySpark files parsed successfully in ~3.7s.

### Interpretation

The test strategy directly protects the platform's core promise. The determinism, idempotency, and
cross-merge tests are not generic coverage — they guard the exact behaviour that makes cross-tool
stitching correct. Coverage targets of **80%+** (100% on the most complex modules) are applied per
parser.

---

## 10. Risks, Limitations & Mitigations

| # | Risk / Limitation | Impact | Likelihood | Mitigation / Recommendation |
|---|---|---|---|---|
| R1 | **No authentication / access control** | High | Certain (by design) | Add auth (SSO/OIDC) + RBAC before any non-trusted deployment. **Top priority.** |
| R2 | **Neo4j + Postgres not transactional together** | Medium | Low | Add reconciliation / retry; document eventual consistency; consider outbox pattern |
| R3 | **Scale unproven on production-volume estates** (except Spark) | Medium | Medium | Run a load test against representative real dumps before go-live |
| R4 | **Dynamic/runtime values produce partial lineage** | Medium | Medium | Already handled via `lineage_partial` markers; ensure UI surfaces them clearly to users |
| R5 | **Identity contract is a hard dependency** | High if mishandled | Low | Govern `lineage-contracts` as a versioned interface with change control |
| R6 | **Scope gaps** (Scala, custom-SQL column lineage, extract introspection, UDF bodies) | Low–Medium | Known | Confirm acceptable for pilot; schedule on roadmap if business needs them |
| R7 | **Source-tool version drift** (e.g., new Tableau XML formats) | Medium | Ongoing | Maintain fixtures; add regression tests when new formats appear |
| R8 | **Operational maturity** (no auth, single-EC2 demo deploy) | Medium | — | Define target operating model before production |

**The single most important pre-production item is R1 (authentication).** Everything else is either
already mitigated, low-likelihood, or a roadmap decision.

---

## 11. Non-Functional Posture

| Attribute | Current posture |
|---|---|
| **Performance** | Spark: 97 files in ~3.7s. Per-parser targets defined (e.g., large scripts in seconds). Enterprise-scale load test outstanding. |
| **Reliability** | Independent parser failure domains; idempotent re-runs; `restart: unless-stopped` on services. |
| **Security** | Credential scrubbing enforced and tested; read-only API guard. **No authN/authZ yet.** |
| **Scalability** | Stateless parsers (horizontally scalable in principle); single Neo4j/Postgres instance today. |
| **Maintainability** | ~800 tests; clear module boundaries; grammar-driven parsers; shared contract. |
| **Operability** | Health endpoints on every service; lifecycle scripts; data-preserving refresh. |
| **Portability** | Docker Compose (dev) and Terraform/AWS (cloud); Windows via WSL2. |

---

## 12. Cost & Deployment

### Deployment options

- **Local / pilot:** Docker Compose. One command (`./start.sh`) brings up the full stack;
  `./refresh.sh` reloads code without losing data.
- **Cloud demo:** Terraform module provisions a **single EC2 instance** running the full stack.

### Indicative cloud cost (single-instance demo)

| Configuration | Approx. monthly cost |
|---|---|
| Always-on (t3.medium + 30GB) | **~$33 / month** |
| Stopped nights & weekends | **~$12 / month** |

These figures are for a demo/pilot footprint. A production deployment with HA, separate database
tiers, and authentication would carry a higher (to-be-estimated) cost and is a roadmap item.

---

## 13. Roadmap & Recommendations

### Recommended sequence

**Phase 1 — Production hardening (highest priority)**
- Authentication (SSO/OIDC) and role-based access control. *(Addresses R1.)*
- Store-consistency handling between Neo4j and Postgres. *(Addresses R2.)*
- Load/scale testing against representative production-volume estates. *(Addresses R3.)*

**Phase 2 — Lineage depth**
- Column-level lineage inside Tableau custom SQL.
- UDF body introspection (Spark).
- Extract/`.hyper` schema introspection (Tableau).

**Phase 3 — Coverage & reach**
- Additional sources as the estate requires (e.g., Scala Spark, further BI/ETL tools).
- Runtime-history ingestion (actual run logs vs. definitions) if operationally valuable.

**Phase 4 — Enterprise operations**
- HA topology, monitoring/alerting, backup/restore runbooks, formal operating model.

### Recommendations to leadership

1. **Approve a pilot** with one or two real teams to validate value on production data — the build
   is ready for this today.
2. **Fund Phase 1 (hardening)** as the gate to any wider rollout; authentication is non-negotiable.
3. **Formally govern the identity contract** as a versioned interface.
4. **Confirm scope boundaries** ([Section 5](#5-scope--in-and-out)) are acceptable for the pilot.

---

## 14. Decisions Requested

| # | Decision | From |
|---|---|---|
| D1 | Endorse the architecture direction, in particular the deterministic-identity stitching model and the four-service split | Architect |
| D2 | Ratify the `lineage-contracts` identity scheme as a governed, versioned interface | Architect |
| D3 | Approve the **scope boundaries** in Section 5 for the pilot | Architect + Delivery Manager |
| D4 | Approve a **pilot** on real data with selected teams | Delivery Manager |
| D5 | Prioritise and resource **Phase 1 hardening** (auth, consistency, scale) before production | Delivery Manager |
| D6 | Confirm the **target operating model** and cloud footprint for production | Architect + Delivery Manager |

---

## 15. Appendix — Glossary

| Term | Plain meaning |
|---|---|
| **Lineage** | The path data takes from its origin to where it is used. |
| **Knowledge graph** | A network of "things" (nodes) and "connections" (edges) — here, the lineage map. |
| **Node / Edge** | A node is a thing (a table, dashboard, job). An edge is a relationship between two things. |
| **Parser** | A program that reads a specific file type and extracts meaning from it. |
| **SHA-256 / deterministic ID** | A fixed "fingerprint" of a name; identical inputs always produce the identical fingerprint. |
| **MERGE** | A graph command that means "create this, but only if it doesn't already exist." |
| **Idempotent** | Doing it twice has the same effect as doing it once (no duplicates). |
| **Partial lineage** | An honestly-flagged "we couldn't be fully sure here" marker rather than a guess. |
| **Upstream / Downstream** | Upstream = where data came from; Downstream = what data feeds into. |
| **Neo4j / Postgres** | The graph database / the SQL database used to store the map. |
| **Gateway** | The single API front door to the platform. |
| **TWS** | IBM Tivoli Workload Scheduler — the enterprise job scheduler. |

---

*Prepared for review by the Solution Architect and Delivery Manager. A companion technical
walkthrough is available in `presentation.md`.*
