# Enterprise Claude Prompt — Generate Business Flow Excel for CP Catalog (API 360 + Data 360)

Copy everything in the box below into your BBH-controlled Enterprise Claude. Then attach
your Swagger/OpenAPI files and your inbound/outbound feed Excel files in the same message.
Claude will produce a single Excel workbook that ingests directly into CP Catalog's API 360
business flows and Data 360 pipeline builder.

---

## THE PROMPT

You are a financial-data integration architect for Brown Brothers Harriman Capital
Partners. You are building business-flow metadata for "CP Catalog", an internal data
catalog for a private banking / SMA investment-management accounting platform (the SEI
Wealth Platform, SWP).

### CRITICAL MIGRATION CONTEXT — read first
BBH is **migrating OFF the legacy AddVantage accounting platform ONTO SEI SWP**. This is a
strangler-fig migration. In every input:
- **AddVantage = LEGACY** (the current system being retired). Where the interface inventory
  shows AddVantage (or STAR/AddV) as Source/Target System, that is the CURRENT/legacy state,
  kept FOR REFERENCE only.
- **SEI SWP = TARGET** (what we are migrating to). The SEI APIs (Swagger), SEI inbound feeds
  (EOD feed workbook), and SEI loaders (loader workbook) are the TARGET implementation that
  REPLACES the AddVantage hop.
- The interface inventory's "Source change to new acct sys = Yes" confirms that interface is
  being re-platformed from AddVantage to SEI.

For every in-scope interface, produce BOTH:
- the **legacy reference** (how it works today on AddVantage), and
- the **SEI target** — which specific SEI API / SEI inbound feed / SEI loader replaces that
  AddVantage hop. Match the AddVantage interface (by Integration/Description, e.g. Accounts,
  Positions) to the corresponding SEI feed (e.g. Account, End of Day Positions), SEI loader,
  or SEI API endpoint in the attached files. Where AddVantage was the Source, the SEI target
  feed/API becomes the new source; where AddVantage was the Target, the SEI loader becomes the
  new publishing mechanism. Do NOT drop AddVantage — record it as legacy_system for traceability.

I will attach:
1. One or more **Swagger / OpenAPI** files (JSON or YAML) describing platform APIs.
2. One **inbound feed** workbook (SWP EOD feeds → BBH; sheets per feed with fields).
3. One **outbound loader/feed** workbook (BBH → SWP loaders; attributes per loader).
4. One **interface inventory** workbook — the system-to-system integration map (the
   AUTHORITATIVE routing source). Its columns are:
   PBDev/PBDW/IM/CRM | Date of Update | In/Out of Scope | Update Owner | Application |
   Integration | Description | Type | Source System | Source 3rd Party or In-House |
   Target System | Target 3rd Party or In-House | Inbound/Outbound With Respect To
   Existing Acct Platform | Direct Feed to Acct Platform Y/N | Feed Routing |
   Intraday | EOD/Overnight | Frequency | Standard/Custom Extract |
   Application Owner/Contact | Source change to new acct sys | Type (Application or
   Extract) | Potential Process Improvement | Notes

Use the interface inventory as the AUTHORITATIVE source for: system-to-system routing
(Source System → Target System), flow direction (Inbound/Outbound w.r.t. the accounting
platform), schedule (Intraday vs EOD/Overnight + Frequency), scope (only build flows for
"In Scope" rows), and the real routing path (Feed Routing, e.g. AddV→PBDW→Client Portal).
The Swagger and feed files supply the field/endpoint detail; the interface inventory
supplies the TRUE integration topology. When they conflict on direction or routing, the
interface inventory wins.

Your job: analyze all three and produce **ONE Excel workbook** named
`CP_Catalog_Business_Flows.xlsx` that defines business flows for both API 360 and Data
360, ready to ingest. Produce ONLY the workbook plus a short coverage summary. Do not
invent endpoints, feeds, or fields that are not present in the attached files — every row
must trace to something in the inputs.

### Business domain context (use to group and name flows)
The platform serves private banking + SMA investment management. Organize flows under
these business domains, only where the inputs support them:
- Account & Client Lifecycle (onboarding, maintenance, closure, transfer)
- Portfolio Accounting (positions, transactions, tax lots, corporate actions, income, cash)
- SMA-specific (model drift, rebalancing, tax-loss harvesting, sleeve/UMA, restriction screening)
- Valuation & Performance (pricing/NAV, performance, reconciliation)
- Fee & Billing (fee computation, fee adjustments)
- Reporting & Regulatory (statements, regulatory extracts, data quality)

### Pipeline archetypes (classify every Data 360 flow as exactly one)
- `inbound_ingest`  — external feed → bronze → silver → gold mart
- `compute_derive`  — existing marts → calculation → new mart (no external feed)
- `outbound_publish`— BBH data → transform → loader file → SFTP to SWP
- `cross_cutting`   — quality gates / lineage / regulatory extracts spanning pipelines

### Produce these 6 sheets, with EXACTLY these columns and header names

**Sheet 1 — `API_Business_Flows`** (one row per flow)
| Flow_ID | Flow_Name | Business_Domain | Goal | Trigger | Primary_Entity | Source_Swagger | Notes |
- Flow_ID: snake_case stable id (e.g. `tax_lot_audit`).
- Goal: one sentence, what the flow accomplishes.
- Trigger: what starts it (user action, schedule, event).
- Primary_Entity: the main business object (e.g. account, taxlot, order).
- Source_Swagger: the API file/source this flow came from.

**Sheet 2 — `API_Flow_Steps`** (one row per step; the ordered endpoint chain)
| Flow_ID | Step_Order | Method | Path | Operation_ID | Produces_Entity | Consumes_Entity | Note |
- Order steps so producers come before consumers; auth/token step first.
- Method/Path/Operation_ID must come VERBATIM from the Swagger (do not paraphrase paths).
- Produces_Entity: the key this step yields downstream (e.g. `accountNumber`, `access_token`).
- Consumes_Entity: comma-separated keys this step needs (must be produced by an earlier step
  or be an input parameter). This is what lets CP Catalog auto-build the dependency graph.
- Use this canonical entity vocabulary where applicable (keep names EXACT for cross-linking):
  access_token, firmId, processingOrgId, platformUser, clientId, externalClientId,
  accountNumber, externalAccountId, portfolioId, modelId, parentActivityId, activityId,
  orderId, tokenId, requestId, loaderId, taxlotId, transactionId, settlementId.

**Sheet 3 — `Data_Pipelines`** (one row per pipeline)
| Pipeline_ID | Pipeline_Name | Business_Domain | Archetype | Direction | Schedule | Legacy_System | SEI_Target_Type | SEI_Target_ID | Source_System | Target_System | Feed_Routing | In_Scope | Goal | Owner | Project_ID | Notes |
- Archetype: one of inbound_ingest | compute_derive | outbound_publish | cross_cutting.
- Direction: inbound | outbound | internal — TAKE FROM the interface inventory's
  "Inbound/Outbound With Respect To Existing Acct Platform" where the pipeline maps to an interface.
- Schedule: EOD | BOD | Intraday | OnDemand — TAKE FROM Intraday / EOD-Overnight / Frequency.
- Legacy_System: the AddVantage (or STAR) interface this replaces, FOR REFERENCE (e.g. "AddVantage Accounts").
- SEI_Target_Type: sei_feed | sei_loader | sei_api — what SEI mechanism implements the target.
- SEI_Target_ID: the exact SEI feed name / loader name / API operationId from the attached files
  (e.g. `End of Day Positions`, `Adhoc_Income_Loader`, `POST /accruals`). This is the migration mapping.
- Source_System / Target_System / Feed_Routing: from the interface inventory (the real topology).
- In_Scope: Yes/No from the interface inventory; only emit pipelines that are In Scope.
- Owner: Application Owner/Contact from the interface inventory where available.
- Project_ID: `sei` unless the inputs indicate another project.

**Sheet 4 — `Pipeline_Stages`** (one row per stage member; the end-to-end order)
| Pipeline_ID | Stage | Stage_Order | Member_Type | Member_ID | Member_Name | System | Note |
- Stage: source_system | inbound_feed | dbt_bronze | dbt_silver | dbt_gold | loader | outbound_feed | target_system.
- Member_Type: system | feed | dbt_model | loader.
- System: for source_system/target_system stages, the system name from the interface inventory
  (e.g. AddVantage, Pivotal CRM, PBDW) so the stages mirror the real routing path.
- Member_ID: the feed/loader/system name from the attached workbooks, or a proposed dbt
  model id (e.g. `sei_brz_positions`, `sei_gld_positions_summary`).
- Build the stage chain to MATCH the Feed_Routing path. Example for "AddV→PBDW→Pivotal":
  source_system(AddVantage) → inbound_feed → dbt_bronze → dbt_silver → dbt_gold(PBDW) →
  outbound_feed → target_system(Pivotal CRM).
- For inbound_ingest: source_system → inbound_feed → dbt_bronze → dbt_silver → dbt_gold.
- For outbound_publish: (source marts) → dbt_silver → loader → outbound_feed → target_system.
- For compute_derive: (input marts as dbt_silver) → dbt_gold (no inbound_feed).

**Sheet 5 — `Interface_360`** (one row per interface, straight from the inventory — for Interface 360)
| Interface_ID | Application | Integration | Description | Legacy_System | SEI_Target_Type | SEI_Target_ID | Migration_Status | Source_System | Target_System | Direction | Direct_Feed | Feed_Routing | Schedule | Frequency | Extract_Type | Scope | Owner | Linked_Pipeline_ID | Project_ID |
- Interface_ID: snake_case from Application + Integration (e.g. `pivotal_crm_positions`).
- Reproduce Source_System, Target_System, Direction, Feed_Routing, Frequency EXACTLY from the inventory.
- Legacy_System: the AddVantage/STAR system in the current routing (e.g. "AddVantage"), for reference.
- SEI_Target_Type / SEI_Target_ID: the SEI feed/loader/API that replaces the AddVantage hop (see migration rules).
- Migration_Status: from "Source change to new acct sys" — "Yes" = being re-platformed to SEI, else "legacy".
- Direction: from "Inbound/Outbound With Respect To Existing Acct Platform".
- Schedule: Intraday or EOD/Overnight (whichever is flagged).
- Linked_Pipeline_ID: the Data_Pipelines Pipeline_ID this interface maps to (blank if none).
- Only emit In-Scope rows.

**Sheet 6 — `Flow_Datapoint_Map`** (links flows to the key data points they touch)
| Flow_or_Pipeline_ID | Module | Datapoint_Normalized | Source_Field | Source_Artifact | Direction | Note |
- Module: api | data.
- Datapoint_Normalized: lowercase normalized name (e.g. `account_number`, `cost_basis`).
- Source_Field: the actual field/attribute name from the feed/loader/swagger.
- Source_Artifact: which feed, loader, or endpoint it came from.
- Direction: inbound | outbound.
- Include the KEY data points each flow reads/writes (account, portfolio, security ids,
  amounts, dates) — enough to drive Datapoint 360 cross-linking, not every field.

**Sheet 7 — `Coverage_Report`** (your audit of what you produced)
| Metric | Value |
- Rows: swagger_files_read, endpoints_found, inbound_feeds_read, outbound_loaders_read,
  interfaces_read, in_scope_interfaces, api_flows_created, data_pipelines_created,
  interfaces_emitted, addvantage_interfaces_mapped_to_sei, addvantage_interfaces_unmapped,
  flow_candidates_skipped, unmatched_consumes, notes.
- addvantage_interfaces_unmapped = legacy AddVantage interfaces you could NOT match to a SEI
  feed/loader/API in the attached files — these are migration gaps to flag for my review. unmatched_consumes = steps whose Consumes_Entity was not produced upstream (flag,
  do not silently fix).

### Rules
- Trace everything to inputs. If you cannot determine a value, leave the cell blank — do
  not guess. Never fabricate an endpoint path, feed name, or field.
- Paths, operationIds, feed names, loader names, and field names must be reproduced
  EXACTLY as they appear in the source files (these are join keys for ingestion).
- Order API steps topologically (producers before consumers; auth first). If a consume has
  no producer, still include the step but record it in unmatched_consumes.
- Prefer 8–20 well-formed flows over exhaustively enumerating every endpoint. Choose flows
  that represent real business processes for private banking / SMA accounting.
- Use ISO date semantics, snake_case ids, and the canonical entity vocabulary above.
- Output the .xlsx file. Keep prose to a 5–8 line summary after the file: how many flows
  and pipelines, which domains covered, and anything in unmatched_consumes worth my review.

---

## After Claude returns the workbook

1. Save it as `CP_Catalog_Business_Flows.xlsx`.
2. Place it where CP Catalog ingestion can read it and set the env var (when the importer
   for this file is added): `BUSINESS_FLOWS_XLSX`.
3. The sheets map to CP Catalog as:
   - `API_Business_Flows` + `API_Flow_Steps` → API 360 business flows (api_business_flows / _steps)
   - `Data_Pipelines` + `Pipeline_Stages` → Data 360 pipeline builder (data_pipelines / pipeline_members)
   - `Interface_360` → Interface 360 (system-to-system integration map)
   - `Flow_Datapoint_Map` → Datapoint 360 cross-links
4. Review `Coverage_Report.unmatched_consumes` first — those are flows where the dependency
   chain is incomplete and may need a missing endpoint or a manual producer.

## Tips for best results
- Attach the **enriched** Swagger if you have it (with produces/consumes hints), but the
  prompt also infers produces/consumes from path params + request/response bodies.
- If you have many Swagger files, attach them together so Claude can build cross-API flows.
- Run per business domain if the inputs are very large (e.g. "only Portfolio Accounting
  flows this run") to stay within limits, then combine the workbooks.
