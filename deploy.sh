#!/usr/bin/env bash
# Deploy ncc-spoke-monitor as a scheduled Cloud Run Job.
#
# Usage:
#   ./deploy.sh [--schedule "*/5 * * * *"]
#
# Defaults to running every 5 minutes. Pass --schedule "" to skip scheduler setup.

set -euo pipefail

# ── Config — edit these ────────────────────────────────────────────────────────
PROJECT_ID="lima-core"
REGION="us-central1"
REPO="ncc-monitor"
IMAGE_NAME="ncc-spoke-monitor"
JOB_NAME="ncc-spoke-monitor"
SA_NAME="ncc-monitor-sa"
SCHEDULE="${SCHEDULE:-*/5 * * * *}"   # override with --schedule flag or env var

# Derived values
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$IMAGE_NAME"
SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
SCHEDULER_JOB="ncc-monitor-trigger"
SCHEDULER_SA="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

# Parse flags
while [[ $# -gt 0 ]]; do
  case $1 in
    --schedule) SCHEDULE="$2"; shift 2 ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

echo "=== NCC Monitor — Cloud Run Job Deployment ==="
echo "  Project : $PROJECT_ID"
echo "  Region  : $REGION"
echo "  Image   : $IMAGE"
echo "  Schedule: ${SCHEDULE:-disabled}"
echo ""

# ── 1. Enable required APIs ────────────────────────────────────────────────────
echo "[1/6] Enabling APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  networkconnectivity.googleapis.com \
  monitoring.googleapis.com \
  --project="$PROJECT_ID"

# ── 2. Artifact Registry repo ─────────────────────────────────────────────────
echo "[2/6] Ensuring Artifact Registry repo '$REPO'..."
if ! gcloud artifacts repositories describe "$REPO" \
    --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
  gcloud artifacts repositories create "$REPO" \
    --repository-format=docker \
    --location="$REGION" \
    --project="$PROJECT_ID"
fi

# ── 3. Service account + IAM ──────────────────────────────────────────────────
echo "[3/5] Ensuring service account '$SA_NAME'..."
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="NCC Monitor Cloud Run SA" \
    --project="$PROJECT_ID"
fi

# Runtime SA roles
for ROLE in \
  roles/monitoring.metricWriter \
  roles/monitoring.dashboardEditor \
  roles/networkconnectivity.viewer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="$ROLE" \
    --condition=None \
    --quiet
done

# Cloud Build SA needs to deploy Cloud Run Jobs and act as the runtime SA
CLOUDBUILD_SA="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')@cloudbuild.gserviceaccount.com"
for ROLE in \
  roles/run.admin \
  roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$CLOUDBUILD_SA" \
    --role="$ROLE" \
    --condition=None \
    --quiet
done

# ── 4. Build, push & deploy via Cloud Build ───────────────────────────────────
echo "[4/5] Submitting Cloud Build (build → push → deploy)..."
gcloud builds submit . \
  --config=cloudbuild.yaml \
  --project="$PROJECT_ID"

# ── 5. Cloud Scheduler trigger ────────────────────────────────────────────────
if [[ -n "$SCHEDULE" ]]; then
  echo "[5/5] Configuring Cloud Scheduler job '$SCHEDULER_JOB' ($SCHEDULE)..."

  # Scheduler needs permission to invoke Cloud Run Jobs
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SCHEDULER_SA" \
    --role="roles/run.invoker" \
    --condition=None \
    --quiet

  JOB_URI="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/$JOB_NAME:run"

  if gcloud scheduler jobs describe "$SCHEDULER_JOB" \
      --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
    gcloud scheduler jobs update http "$SCHEDULER_JOB" \
      --schedule="$SCHEDULE" \
      --uri="$JOB_URI" \
      --oauth-service-account-email="$SCHEDULER_SA" \
      --location="$REGION" \
      --project="$PROJECT_ID"
  else
    gcloud scheduler jobs create http "$SCHEDULER_JOB" \
      --schedule="$SCHEDULE" \
      --uri="$JOB_URI" \
      --oauth-service-account-email="$SCHEDULER_SA" \
      --location="$REGION" \
      --project="$PROJECT_ID"
  fi
else
  echo "[5/5] Skipping Cloud Scheduler (--schedule is empty)."
fi

echo ""
echo "=== Done ==="
echo ""
echo "Run manually:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID --wait"
echo ""
echo "View logs:"
echo "  gcloud logging read 'resource.type=cloud_run_job AND resource.labels.job_name=$JOB_NAME' \\"
echo "    --project=$PROJECT_ID --limit=50 --format='table(timestamp,textPayload)'"
echo ""
echo "Dashboard:"
echo "  https://console.cloud.google.com/monitoring/dashboards?project=$PROJECT_ID"
