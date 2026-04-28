# GCP Network Connectivity Center Monitor

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

A scheduled Cloud Run Job that polls the GCP Network Connectivity Center (NCC), Compute, and Cloud Monitoring APIs every 5 minutes to write custom metrics and maintain a live Cloud Monitoring dashboard covering NCC spoke health, BGP sessions, VPN tunnels, Interconnect attachments, and Cloud Router prefix quotas.

## How it works

`ncc_spoke_monitor.py` runs as a one-shot Python script inside a Cloud Run Job. On each execution it:

1. Ensures all custom metric descriptors exist in Cloud Monitoring
2. Fetches NCC spoke state, hub route tables, Cloud Router BGP peer status, VPN tunnel status, and Interconnect VLAN attachment status from the GCP APIs
3. Writes a time-series data point for each resource to Cloud Monitoring
4. Creates or updates a mosaic dashboard named **"NCC Comprehensive Monitor"** in the project

Cloud Scheduler triggers the job on a cron schedule (default: `*/5 * * * *`).

## Custom metrics written

| Metric | Description |
|---|---|
| `custom.googleapis.com/ncc/spoke_status` | 1 = ACTIVE, 0 = other, per spoke |
| `custom.googleapis.com/ncc/spoke_count` | Spoke count grouped by hub and state |
| `custom.googleapis.com/ncc/route_count` | NCC route count per hub/route-table |
| `custom.googleapis.com/ncc/bgp_peer_up` | 1 = UP, 0 = DOWN, per BGP peer |
| `custom.googleapis.com/ncc/bgp_learned_routes` | Routes learned per BGP peer |
| `custom.googleapis.com/ncc/vpn_tunnel_up` | 1 = ESTABLISHED, 0 = other, per tunnel |
| `custom.googleapis.com/ncc/interconnect_up` | 1 = OS_ACTIVE, 0 = other, per attachment |

Platform metrics (`compute.googleapis.com/quota/cloud_router_prefixes_*`) are referenced in the dashboard but written automatically by GCP.

> **Note:** `compute.googleapis.com/router/bgp/*` per-session metrics are only emitted for Dedicated/Partner Interconnect, not for HA VPN or NCC VPN spokes.

## Prerequisites

- A GCP project with billing enabled
- `gcloud` CLI authenticated with sufficient permissions
- Docker (for local builds)
- Terraform >= 1.5 and the `hashicorp/google` provider ~> 5.0 (for the Terraform path)

## Deployment

### Option A — Shell script (quickest)

Edit the config block at the top of [deploy.sh](deploy.sh) to set your `PROJECT_ID` and `REGION`, then run:

```bash
./deploy.sh
```

To use a custom schedule or disable Cloud Scheduler:

```bash
./deploy.sh --schedule "*/10 * * * *"   # every 10 minutes
./deploy.sh --schedule ""               # build and deploy only, no scheduler
```

The script enables all required APIs, creates the Artifact Registry repository and service account, submits a Cloud Build job (build → push → Cloud Run Job create/update), and configures Cloud Scheduler.

### Option B — Terraform

```bash
cd terraform
terraform init
terraform apply -var="project_id=YOUR_PROJECT_ID"
```

Terraform manages APIs, IAM, Artifact Registry, and Cloud Scheduler. The Cloud Run Job lifecycle is handled by a `null_resource` that calls `gcloud builds submit` via `cloudbuild.yaml` whenever source files change.

Key variables (all have defaults — see [terraform/variables.tf](terraform/variables.tf)):

| Variable | Default | Description |
|---|---|---|
| `project_id` | `lima-core` | GCP project ID |
| `region` | `us-central1` | Region for all regional resources |
| `repo_name` | `ncc-monitor` | Artifact Registry repository name |
| `image_name` | `ncc-spoke-monitor` | Docker image name |
| `job_name` | `ncc-spoke-monitor` | Cloud Run Job name |
| `sa_name` | `ncc-monitor-sa` | Service account ID |
| `schedule` | `*/5 * * * *` | Cron expression; set to `""` to disable |

## IAM roles required

The runtime service account (`ncc-monitor-sa`) needs:

| Role | Purpose |
|---|---|
| `roles/monitoring.metricWriter` | Write custom time-series data |
| `roles/monitoring.dashboardEditor` | Create/update the monitoring dashboard |
| `roles/networkconnectivity.viewer` | Read NCC hubs, spokes, and route tables |
| `roles/run.invoker` | Allow Cloud Scheduler to trigger the Cloud Run Job |

The Cloud Build default service account also needs `roles/run.admin` and `roles/iam.serviceAccountUser` to deploy the Cloud Run Job.

## GCP APIs enabled

- `run.googleapis.com`
- `cloudscheduler.googleapis.com`
- `cloudbuild.googleapis.com`
- `artifactregistry.googleapis.com`
- `networkconnectivity.googleapis.com`
- `monitoring.googleapis.com`

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `PROJECT_ID` | Yes | GCP project ID — the script exits immediately if unset |

## Running manually

After deployment, trigger a single execution:

```bash
gcloud run jobs execute ncc-spoke-monitor \
  --region=us-central1 \
  --project=YOUR_PROJECT_ID \
  --wait
```

View logs:

```bash
gcloud logging read \
  'resource.type=cloud_run_job AND resource.labels.job_name=ncc-spoke-monitor' \
  --project=YOUR_PROJECT_ID \
  --limit=50 \
  --format='table(timestamp,textPayload)'
```

## Local development

Install dependencies and run directly with Application Default Credentials:

```bash
pip install -r requirements.txt
gcloud auth application-default login
PROJECT_ID=YOUR_PROJECT_ID python3 ncc_spoke_monitor.py
```

## Project structure

```
.
├── ncc_spoke_monitor.py   # Monitor script (entrypoint)
├── requirements.txt       # Python dependencies
├── Dockerfile             # Container image (python:3.11-slim)
├── cloudbuild.yaml        # Cloud Build pipeline (build → push → deploy job)
├── deploy.sh              # One-shot shell deployment script
└── terraform/
    ├── main.tf            # Provider config and locals
    ├── variables.tf       # Input variables
    ├── apis.tf            # API enablement
    ├── registry.tf        # Artifact Registry repository
    ├── iam.tf             # Service accounts and IAM bindings
    ├── build.tf           # null_resource wrapping Cloud Build
    ├── scheduler.tf       # Cloud Scheduler job
    └── outputs.tf         # Useful URLs and commands
```

## Dashboard

After the first run, the dashboard is available at:

```
https://console.cloud.google.com/monitoring/dashboards?project=YOUR_PROJECT_ID
```

The dashboard is named **"NCC Comprehensive Monitor"** and is updated on every job execution. It contains four sections:

1. **NCC Spoke Health** — per-spoke status and counts by hub/state/location
2. **NCC Route Inventory** — control-plane route counts per hub route table
3. **Link Health** — BGP sessions, VPN tunnels, and Interconnect attachments (data-plane signals)
4. **Cloud Router Prefix Quotas** — cross-region and own-region prefix usage vs. limit per VPC network
