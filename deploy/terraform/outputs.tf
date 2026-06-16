output "target_platform" {
  value = var.target_platform
}

output "registry_url" {
  value       = local.cluster_registry
  description = "Container registry hosts the service images should be pushed to."
}

output "object_storage" {
  value = (
    var.target_platform == "azure" ? module.azure[0].object_storage :
    var.target_platform == "aws"   ? module.aws[0].object_storage   :
    var.target_platform == "gcp"   ? module.gcp[0].object_storage   :
    null
  )
  description = "Provisioned object storage identifier (blob container / S3 bucket / GCS bucket)."
}

output "kubeconfig_command" {
  value = (
    var.target_platform == "azure" ? "az aks get-credentials -g ${module.azure[0].resource_group} -n ${module.azure[0].cluster_name}" :
    var.target_platform == "aws"   ? "aws eks update-kubeconfig --region ${var.region} --name ${module.aws[0].cluster_name}" :
    var.target_platform == "gcp"   ? "gcloud container clusters get-credentials ${module.gcp[0].cluster_name} --region ${var.region} --project ${var.gcp.project_id}" :
    var.target_platform == "kubernetes" ? "# Using existing kubeconfig: ${var.kubernetes_byo.kubeconfig_path}" :
    "# docker target — no kubeconfig"
  )
  description = "Command to refresh local kubeconfig for the provisioned cluster."
}

output "docker_endpoints" {
  value       = var.target_platform == "docker" ? module.docker[0].endpoints : {}
  description = "When target_platform=docker, local URLs for each service."
}
