locals {
  resource_base = "${var.name_prefix}-${var.stack}-${var.environment}"
  tags = {
    project     = "azure-healthcare-digital-quality-platform"
    stack       = var.stack
    environment = var.environment
    managed_by  = "terraform"
  }
}

# ---------------------------------------------------------------------------
# Cloud / cluster provisioning (one of). Each module returns:
#   - cluster        : kubeconfig-style auth object
#   - registry_url   : container registry hostname/url
#   - object_storage : bucket/container identifier
# ---------------------------------------------------------------------------

module "azure" {
  count  = var.target_platform == "azure" ? 1 : 0
  source = "./modules/azure"

  name          = local.resource_base
  location      = var.region
  resource_group = var.azure.resource_group
  node_size     = var.azure.aks_node_size
  node_count    = var.azure.aks_node_count
  tags          = local.tags
}

module "aws" {
  count  = var.target_platform == "aws" ? 1 : 0
  source = "./modules/aws"

  name        = local.resource_base
  region      = var.region
  eks_version = var.aws.eks_version
  node_size   = var.aws.node_size
  node_count  = var.aws.node_count
  vpc_cidr    = var.aws.vpc_cidr
  tags        = local.tags
}

module "gcp" {
  count  = var.target_platform == "gcp" ? 1 : 0
  source = "./modules/gcp"

  name       = local.resource_base
  region     = var.region
  project_id = var.gcp.project_id
  node_size  = var.gcp.node_size
  node_count = var.gcp.node_count
  labels     = local.tags
}

module "kubernetes" {
  count  = var.target_platform == "kubernetes" ? 1 : 0
  source = "./modules/kubernetes"

  kubeconfig_path    = var.kubernetes_byo.kubeconfig_path
  kubeconfig_context = var.kubernetes_byo.kubeconfig_context
  registry           = var.kubernetes_byo.registry
}

module "docker" {
  count  = var.target_platform == "docker" ? 1 : 0
  source = "./modules/docker"

  name          = local.resource_base
  stack         = var.stack
  services      = var.services
  image_tag     = var.image_tag
  build_context = var.docker_local.build_context
  host_port_map = var.docker_local.host_port_map
}

# ---------------------------------------------------------------------------
# Application deployment onto whichever Kubernetes cluster was provisioned
# (skipped when target_platform = docker — docker module runs containers
# directly).
# ---------------------------------------------------------------------------

module "workload" {
  count  = var.target_platform != "docker" ? 1 : 0
  source = "./modules/k8s_workload"

  namespace    = var.namespace
  stack        = var.stack
  services     = var.services
  image_tag    = var.image_tag
  registry_url = local.cluster_registry
  labels       = local.tags

  depends_on = [
    module.azure,
    module.aws,
    module.gcp,
    module.kubernetes,
  ]
}

locals {
  cluster_registry = (
    var.target_platform == "azure"      ? module.azure[0].registry_url      :
    var.target_platform == "aws"        ? module.aws[0].registry_url        :
    var.target_platform == "gcp"        ? module.gcp[0].registry_url        :
    var.target_platform == "kubernetes" ? module.kubernetes[0].registry_url :
    ""
  )
}
