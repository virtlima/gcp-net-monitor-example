# Cloud Build is imperative (submit a build, get an image + deployed job), so it
# is bridged into Terraform via a null_resource. The triggers block re-runs the
# build whenever any source file changes, keeping the deployed image in sync.
#
# Cloud Build (via cloudbuild.yaml) owns the Cloud Run Job lifecycle — it creates
# the job on first run and updates the image on subsequent runs. Terraform does
# not manage a google_cloud_run_v2_job resource to avoid state conflicts.

resource "null_resource" "cloud_build" {
  triggers = {
    cloudbuild_yaml = filesha256("${local.source_root}/cloudbuild.yaml")
    dockerfile      = filesha256("${local.source_root}/Dockerfile")
    script          = filesha256("${local.source_root}/ncc_spoke_monitor.py")
    requirements    = filesha256("${local.source_root}/requirements.txt")
  }

  provisioner "local-exec" {
    command = <<-EOT
      gcloud builds submit ${local.source_root} \
        --config=${local.source_root}/cloudbuild.yaml \
        --project=${var.project_id}
    EOT
  }

  depends_on = [
    google_artifact_registry_repository.ncc_monitor,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_sa_user,
    google_project_service.apis,
  ]
}
