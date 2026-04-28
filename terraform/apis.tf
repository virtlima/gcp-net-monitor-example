locals {
  required_apis = toset([
    "run.googleapis.com",
    "cloudscheduler.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "networkconnectivity.googleapis.com",
    "monitoring.googleapis.com",
  ])
}

resource "google_project_service" "apis" {
  for_each = local.required_apis

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
