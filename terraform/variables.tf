variable "project_id" {
  description = "GCP project ID."
  type        = string
  default     = "lima-core"
}

variable "region" {
  description = "GCP region for all regional resources."
  type        = string
  default     = "us-central1"
}

variable "repo_name" {
  description = "Artifact Registry repository name."
  type        = string
  default     = "ncc-monitor"
}

variable "image_name" {
  description = "Docker image name inside the Artifact Registry repo."
  type        = string
  default     = "ncc-spoke-monitor"
}

variable "job_name" {
  description = "Cloud Run Job name."
  type        = string
  default     = "ncc-spoke-monitor"
}

variable "sa_name" {
  description = "Service account ID used by the Cloud Run Job and Cloud Scheduler."
  type        = string
  default     = "ncc-monitor-sa"
}

variable "schedule" {
  description = "Cron expression for Cloud Scheduler (e.g. '*/5 * * * *'). Set to empty string to disable."
  type        = string
  default     = "*/5 * * * *"
}
