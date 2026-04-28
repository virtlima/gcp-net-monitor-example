terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Resolve the numeric project number (needed for the Cloud Build default SA).
data "google_project" "project" {
  project_id = var.project_id
}

locals {
  sa_email        = "${var.sa_name}@${var.project_id}.iam.gserviceaccount.com"
  cloudbuild_sa   = "${data.google_project.project.number}@cloudbuild.gserviceaccount.com"
  image_base      = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repo_name}/${var.image_name}"
  # Path to the repo root relative to the terraform/ directory.
  source_root     = "${path.module}/.."
}
