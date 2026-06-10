# SEI Accounting Demo
**BBH Capital Partners SWP Migration — Airflow 3.2.1 + dbt**

End-to-end demo of the SEI → BBH accounting transformation pipeline running inside an **ODH VSCode workbench** (OpenShift Data Hub) with Airflow 3.2.1 and dbt pre-installed.

---

## What this demo covers

| Scenario | What it shows |
|---|---|
| **API error handling** | Retry + backoff, circuit breaker, error catalog lookup, auto-fix, alert escalation |
| **Medallion pipeline** | Bronze → Silver → Gold dbt models against the SEI accounting API |
| **Late file arrival** | SLA breach alert, grace-period poll, auto-resume when file lands |
| **Backfill** | Historical partition insert for late-add records, idempotent |
| **Bad data correction** | AMENDED_BY_CLIENT → SCD Type 2 expire+insert, material change escalation, audit trail |

---

## Repository layout

```
sei-accounting-demo/
│
├── mock_sei_api/                  # Flask app mimicking SEI SWP API
│   ├── app.py                     # Core endpoints + error injection
│   └── file_routes.py             # Feed status, correction manifest, audit log
│
├── sei_client/                    # Reusable API client
│   └── client.py                  # Retry, circuit breaker, classify_and_handle_error()
│
├── dags/                          # Airflow 3.2.1 DAGs
│   ├── sei_accounting_transform.py          # DAG 1: API extract + dbt medallion
│   ├── sei_file_ingestion_with_backfill.py  # DAG 2: file arrival + backfill + correction
│   └── sei_accounting_demo/
│       └── sei_client/            # Package copy — importable from DAG tasks
│
├── dbt_project/                   # dbt models (bronze / silver / gold)
│   ├── dbt_project.yml
│   ├── profiles.yml.example
│   └── models/
│       ├── bronze/bronze_sei_transactions.sql
│       ├── silver/silver_transactions_conformed.sql
│       ├── silver/silver_transactions_scd2.sql
│       └── gold/gold_portfolio_performance.sql
│
├── scripts/
│   ├── setup.sh                   # One-shot ODH setup
│   ├── demo_start.sh              # Start API + deploy DAGs
│   └── demo_curl_errors.sh        # All curl demo commands
│
├── demo_runner.py                 # Interactive API error scenario walkthrough
├── demo_file_scenarios.py         # Interactive file scenario walkthrough
│
├── .env.example                   # All environment variables
├── Makefile                       # Dev shortcuts
└── requirements.txt
```

---

## Quick start — ODH VSCode workbench

### 1. Clone and run setup

Open a terminal inside your ODH VSCode workbench (the image with Airflow 3.2.1 + dbt).

```bash
git clone <your-repo-url> sei-accounting-demo
cd sei-accounting-demo
bash scripts/setup.sh
```

`setup.sh` does everything:
- Installs `flask` and `requests`
- Creates `.env` from `.env.example`
- Initialises the Airflow SQLite DB and creates `admin` user
- Deploys both DAGs and the `sei_client` package into `$AIRFLOW_HOME/dags`
- Starts the mock SEI API on `:5001`
- Runs smoke tests

### 2. Start Airflow (if not already running in the image)

```bash
# In two separate terminals (or use & to background)
airflow scheduler &
airflow webserver --port 8080 &
```

Then open the Airflow UI via the ODH proxy URL (typically shown in the workbench launcher).

Login: **admin / admin**

---

## Running the demos

### Option A — Interactive terminal walkthrough (no Airflow needed)

```bash
# Error handling: retry, circuit breaker, auto-fix, alert
make demo

# File scenarios: late arrival, backfill, bad data correction
make demo-file

# Raw curl commands (good for showing to an audience)
make demo-errors
```

### Option B — Airflow DAG runs (full graph visualization)

```bash
# Trigger DAG 1: API extract → dbt bronze/silver/gold
make trigger-dag1

# Trigger DAG 2: normal file run
make trigger-dag2

# Trigger DAG 2: simulate late file (SLA breach alert fires)
make trigger-dag2-late

# Trigger DAG 2: corrective feed (bad data + SCD2)
make trigger-dag2-correction
```

Watch the task graph in the Airflow UI at `http://localhost:8080`.

### Option C — Manual curl commands

```bash
BASE="http://localhost:5001"
H="X-API-Key: demo-key-bbh-001"

# Accounts
curl -H "$H" $BASE/v1/accounts

# Trigger 429 rate limit (run 3x fast)
curl -H "$H" $BASE/v1/accounts/ACC001/transactions
curl -H "$H" $BASE/v1/accounts/ACC001/transactions
curl -H "$H" $BASE/v1/accounts/ACC001/transactions

# Suspended account NAV (returns 422 + error_detail)
curl -H "$H" $BASE/v1/accounts/ACC003/nav

# Error catalog
curl -H "$H" $BASE/v1/errors/PRICE_STALE
curl -H "$H" $BASE/v1/errors/ACCOUNT_SUSPENDED

# Auto-fix (fixable)
curl -X POST -H "$H" $BASE/v1/errors/PRICE_STALE/fix

# Auto-fix (not fixable → 409 → escalation)
curl -X POST -H "$H" $BASE/v1/errors/ACCOUNT_SUSPENDED/fix

# Pre-transform validation gate
curl -X POST -H "$H" -H "Content-Type: application/json" \
  -d '{"account_ids":["ACC001","ACC002","ACC003"]}' \
  $BASE/v1/pipeline/validate

# Feed status (Scenario A)
curl -H "$H" "$BASE/v1/feeds/status?date=2025-05-15"

# Correction manifest (Scenario C)
curl -H "$H" $BASE/v1/feeds/corrections/2025-05-15

# Audit trail
curl -H "$H" $BASE/v1/audit/corrections
```

---

## Infrastructure setup (ODH / OpenShift)

### Environment variables

Copy `.env.example` to `.env` and configure:

| Variable | Default | Notes |
|---|---|---|
| `SEI_API_BASE_URL` | `http://localhost:5001` | Point to mock API |
| `SEI_API_KEY` | `demo-key-bbh-001` | Auth header value |
| `SEI_FILE_DROP_DIR` | `/opt/airflow/file_drop` | Must be writable in pod |
| `SEI_SLA_HOUR` | `8` | 08:00 UTC SLA deadline |
| `SEI_SLA_GRACE_MINS` | `60` | Grace period before hard fail |
| `AIRFLOW_HOME` | `/opt/airflow` | Set by ODH image |
| `DBT_PROJECT_DIR` | `/opt/airflow/dbt_project` | Copy dbt_project here |

### Airflow 3.2.1 — import compatibility

The DAGs use the correct Airflow 3.2.1 import paths:

```python
# ✅ Airflow 3.2.1
from airflow.sdk import DAG
from airflow.providers.standard.operators.python import PythonOperator, BranchPythonOperator
from airflow.providers.standard.operators.empty import EmptyOperator

# ❌ Old (Airflow 2.x — will fail on 3.x)
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.empty import EmptyOperator
```

### DAG package import path

The `sei_client` package is deployed alongside the DAGs:

```
$AIRFLOW_HOME/dags/
  sei_accounting_transform.py
  sei_file_ingestion_with_backfill.py
  sei_accounting_demo/
    __init__.py
    sei_client/
      __init__.py
      client.py          ← imported by both DAGs
```

`make deploy-dags` handles this automatically. If you copy files manually:

```bash
DAGS=/opt/airflow/dags
mkdir -p $DAGS/sei_accounting_demo/sei_client
cp sei_client/client.py $DAGS/sei_accounting_demo/sei_client/
touch $DAGS/sei_accounting_demo/__init__.py
touch $DAGS/sei_accounting_demo/sei_client/__init__.py
cp dags/*.py $DAGS/
```

### dbt setup

```bash
# Copy profiles template
cp dbt_project/profiles.yml.example dbt_project/profiles.yml

# Edit target — default is 'dev' (duckdb, no credentials needed)
# Switch to 'oracle' or 'snowflake' for production targets
nano dbt_project/profiles.yml

# Test connection
cd dbt_project && dbt debug --profiles-dir .

# Run models
cd dbt_project && dbt run --profiles-dir . --project-dir .
```

### Resource requirements (ODH workbench)

The demo is designed for the **2 CPU / 4 GB RAM** workbench size:

| Component | CPU | Memory |
|---|---|---|
| Mock SEI API (Flask) | ~0.05 vCPU | ~60 MB |
| Airflow scheduler | ~0.3 vCPU | ~400 MB |
| Airflow webserver | ~0.2 vCPU | ~350 MB |
| dbt run (dev/duckdb) | ~0.2 vCPU burst | ~200 MB |
| **Total** | **~0.75 vCPU** | **~1 GB** |

Leaves ~1.25 vCPU and ~3 GB headroom for the VSCode process itself.

---

## Error scenarios reference

| Error code | HTTP | Severity | Auto-fixable | Demo trigger |
|---|---|---|---|---|
| `RATE_LIMIT` | 429 | TRANSIENT | ✅ (backoff) | Call ACC001 transactions 3× |
| `PRICE_STALE` | 200 (in errors[]) | WARN | ✅ | GET /v1/accounts/ACC002/transactions |
| `ACCOUNT_SUSPENDED` | 200 (in warnings[]) | ERROR | ❌ → alert | GET /v1/accounts/ACC003 |
| `NAV_CALCULATION` | 422 | ERROR | ❌ → alert | GET /v1/accounts/ACC003/nav |
| `SLA_BREACH` | — | WARN | ❌ → ops | trigger-dag2-late |
| `MATERIAL_AMENDMENT` | — | ERROR | ❌ → ops sign-off | trigger-dag2-correction |

### Circuit breaker behaviour

The client circuit breaker (`sei_client/client.py`) opens after **3 consecutive failures** on the same endpoint group and blocks all calls for **10 seconds** before entering HALF-OPEN for recovery testing. This prevents cascade failure to the SEI API under load.

---

## DAG graph overview

### DAG 1 — `sei_accounting_transform`
```
start
  ├── fetch_accounts    ─┐
  ├── fetch_transactions ├──► validate_data ──► branch_on_validation
  └── fetch_nav         ─┘                          │
                                           ┌─────────┴─────────┐
                                      halt_pipeline       dbt_run_bronze
                                                               │
                                                          dbt_run_silver
                                                               │
                                                          dbt_run_gold
                                                               │
                                                          load_summary
                                                               └──► end
```

### DAG 2 — `sei_file_ingestion_with_backfill`
```
start ──► check_sla_status
               ├── past_sla_alert ──┐
               └── wait_for_file ◄──┘
                       │
               parse_and_stage_file
                       │
               branch_on_record_types
                  ┌────┼────────────┐
          normal  │  amended        │  late_adds
                  │                 │
               process_normal  process_amended  process_late_add
                  └────┬────────────┘
                 write_audit_log
                       │
              dbt_targeted_remediation
                       │
                 final_summary ──► end
```

---

## Makefile reference

```
make install          Install flask + requests
make api              Start mock SEI API (foreground)
make api-bg           Start mock SEI API (background)
make api-stop         Stop background API
make airflow-init     Create Airflow DB + admin user
make airflow-up       Start scheduler + webserver
make deploy-dags      Copy DAGs to AIRFLOW_HOME/dags
make trigger-dag1     Trigger sei_accounting_transform
make trigger-dag2     Trigger sei_file_ingestion_with_backfill
make trigger-dag2-late     With simulate_late_file=true
make trigger-dag2-correction  With corrective feed scenario
make demo             Interactive API error demo
make demo-file        Interactive file scenario demo
make demo-errors      All curl commands automated
make dbt-run          Full dbt medallion run
make dbt-bronze       Bronze layer only
make dbt-silver       Silver layer only
make dbt-gold         Gold layer only
make lint             ruff + black
make test             pytest
make clean            Remove caches and temp files
```

---

## Troubleshooting

**Import error on DAG load: `No module named 'sei_accounting_demo'`**
Run `make deploy-dags` — the package needs to be in `$AIRFLOW_HOME/dags/sei_accounting_demo/`.

**Mock API not reachable from DAG task**
In ODH, DAG tasks run in the same pod as the scheduler. The mock API binds to `0.0.0.0:5001` so `localhost:5001` works. Confirm with `curl http://localhost:5001/health` in the workbench terminal.

**Airflow webserver not accessible**
Access via the ODH Route/proxy URL shown in the workbench launcher, not `localhost:8080` directly. The code-server reverse proxy exposes it at a path like `/proxy/8080/`.

**`BranchPythonOperator` import error**
Ensure you're using the 3.2.1-compatible import:
```python
from airflow.providers.standard.operators.python import BranchPythonOperator
```

**dbt profile not found**
```bash
cp dbt_project/profiles.yml.example dbt_project/profiles.yml
cd dbt_project && dbt debug --profiles-dir .
```

**Port 5001 already in use**
```bash
make api-stop           # kills existing process
MOCK_SEI_API_PORT=5002 make api
```
Then update `SEI_API_BASE_URL=http://localhost:5002` in `.env`.
