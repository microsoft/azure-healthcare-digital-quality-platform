provider "azurerm" {
  features {}
  subscription_id = var.azure.subscription_id != "" ? var.azure.subscription_id : null
}

provider "aws" {
  region = var.target_platform == "aws" ? var.region : "us-east-1"
}

provider "google" {
  project = var.gcp.project_id != "" ? var.gcp.project_id : null
  region  = var.target_platform == "gcp" ? var.region : null
}

provider "docker" {}

# kubernetes / helm providers are configured dynamically from whichever
# cluster module produced a kubeconfig. We collapse the per-target outputs
# into a single object and feed that into the providers.

locals {
  cluster = (
    var.target_platform == "azure" ? module.azure[0].cluster :
    var.target_platform == "aws"   ? module.aws[0].cluster   :
    var.target_platform == "gcp"   ? module.gcp[0].cluster   :
    var.target_platform == "kubernetes" ? module.kubernetes[0].cluster :
    null
  )
}

provider "kubernetes" {
  host                   = try(local.cluster.host, "")
  cluster_ca_certificate = try(local.cluster.ca_certificate, null)
  token                  = try(local.cluster.token, null)
  client_certificate     = try(local.cluster.client_certificate, null)
  client_key             = try(local.cluster.client_key, null)
  config_path            = try(local.cluster.config_path, null)
  config_context         = try(local.cluster.config_context, null)
}

provider "helm" {
  kubernetes {
    host                   = try(local.cluster.host, "")
    cluster_ca_certificate = try(local.cluster.ca_certificate, null)
    token                  = try(local.cluster.token, null)
    client_certificate     = try(local.cluster.client_certificate, null)
    client_key             = try(local.cluster.client_key, null)
    config_path            = try(local.cluster.config_path, null)
    config_context         = try(local.cluster.config_context, null)
  }
}
