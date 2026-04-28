resource "google_artifact_registry_repository" "ncc_monitor" {
  project       = var.project_id
  location      = var.region
  repository_id = var.repo_name
  format        = "DOCKER"
  description   = "NCC monitor container images"

  depends_on = [google_project_service.apis]
}
