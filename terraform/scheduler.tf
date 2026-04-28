resource "google_cloud_scheduler_job" "ncc_monitor" {
  # Skip the scheduler entirely when var.schedule is empty.
  count = var.schedule != "" ? 1 : 0

  project  = var.project_id
  name     = "ncc-monitor-trigger"
  location = var.region
  schedule = var.schedule

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${var.job_name}:run"

    oauth_token {
      service_account_email = google_service_account.ncc_monitor.email
    }
  }

  depends_on = [
    google_project_service.apis,
    # Ensure the job exists before the scheduler tries to trigger it.
    null_resource.cloud_build,
  ]
}
