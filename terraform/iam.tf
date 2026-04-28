# ── Runtime service account (used by Cloud Run Job and Cloud Scheduler) ────────

resource "google_service_account" "ncc_monitor" {
  account_id   = var.sa_name
  display_name = "NCC Monitor Cloud Run SA"
  project      = var.project_id
}

# Roles the SA needs at runtime to write metrics and read NCC state.
resource "google_project_iam_member" "runtime_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.ncc_monitor.email}"
}

resource "google_project_iam_member" "runtime_dashboard_editor" {
  project = var.project_id
  role    = "roles/monitoring.dashboardEditor"
  member  = "serviceAccount:${google_service_account.ncc_monitor.email}"
}

resource "google_project_iam_member" "runtime_ncc_viewer" {
  project = var.project_id
  role    = "roles/networkconnectivity.viewer"
  member  = "serviceAccount:${google_service_account.ncc_monitor.email}"
}

# Cloud Scheduler invokes the Cloud Run Job HTTP endpoint using this SA.
resource "google_project_iam_member" "runtime_run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.ncc_monitor.email}"
}

# ── Cloud Build default SA ─────────────────────────────────────────────────────
# The build step in cloudbuild.yaml runs `gcloud run jobs create/update`, so the
# Cloud Build SA needs run.admin and the ability to impersonate the runtime SA.

resource "google_project_iam_member" "cloudbuild_run_admin" {
  project    = var.project_id
  role       = "roles/run.admin"
  member     = "serviceAccount:${local.cloudbuild_sa}"
  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "cloudbuild_sa_user" {
  project    = var.project_id
  role       = "roles/iam.serviceAccountUser"
  member     = "serviceAccount:${local.cloudbuild_sa}"
  depends_on = [google_project_service.apis]
}
