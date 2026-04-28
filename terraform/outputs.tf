output "artifact_registry_repo" {
  description = "Full Artifact Registry repository path."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repo_name}"
}

output "service_account_email" {
  description = "Email of the runtime service account."
  value       = google_service_account.ncc_monitor.email
}

output "cloud_run_job_console_url" {
  description = "Cloud Run Job in the GCP console."
  value       = "https://console.cloud.google.com/run/jobs/details/${var.region}/${var.job_name}?project=${var.project_id}"
}

output "dashboard_console_url" {
  description = "Cloud Monitoring dashboards for this project."
  value       = "https://console.cloud.google.com/monitoring/dashboards?project=${var.project_id}"
}

output "manual_execute_command" {
  description = "Command to trigger the job manually."
  value       = "gcloud run jobs execute ${var.job_name} --region=${var.region} --project=${var.project_id} --wait"
}

output "manual_build_command" {
  description = "Command to rebuild and redeploy without running terraform apply."
  value       = "gcloud builds submit ${path.module}/.. --config=${path.module}/../cloudbuild.yaml --project=${var.project_id}"
}
