variable "name"           { type = string }
variable "location"       { type = string }
variable "resource_group" { type = string }
variable "node_size"      { type = string }
variable "node_count"     { type = number }
variable "tags"           { type = map(string) }

locals {
  rg_name        = var.resource_group != "" ? var.resource_group : "${var.name}-rg"
  acr_name       = replace("${var.name}acr", "-", "")
  storage_name   = substr(replace("${var.name}sa", "-", ""), 0, 24)
  cluster_name   = "${var.name}-aks"
}

resource "azurerm_resource_group" "this" {
  name     = local.rg_name
  location = var.location
  tags     = var.tags
}

resource "azurerm_container_registry" "this" {
  name                = local.acr_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Standard"
  admin_enabled       = false
  tags                = var.tags
}

resource "azurerm_storage_account" "this" {
  name                     = local.storage_name
  resource_group_name      = azurerm_resource_group.this.name
  location                 = azurerm_resource_group.this.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
  tags                     = var.tags
}

resource "azurerm_storage_container" "data" {
  name                  = "data"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_kubernetes_cluster" "this" {
  name                = local.cluster_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  dns_prefix          = local.cluster_name

  default_node_pool {
    name       = "system"
    vm_size    = var.node_size
    node_count = var.node_count
  }

  identity { type = "SystemAssigned" }

  tags = var.tags
}

resource "azurerm_role_assignment" "aks_acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.this.kubelet_identity[0].object_id
}

output "cluster" {
  value = {
    host                   = azurerm_kubernetes_cluster.this.kube_config[0].host
    ca_certificate         = base64decode(azurerm_kubernetes_cluster.this.kube_config[0].cluster_ca_certificate)
    client_certificate     = base64decode(azurerm_kubernetes_cluster.this.kube_config[0].client_certificate)
    client_key             = base64decode(azurerm_kubernetes_cluster.this.kube_config[0].client_key)
    token                  = null
    config_path            = null
    config_context         = null
  }
  sensitive = true
}

output "registry_url"   { value = azurerm_container_registry.this.login_server }
output "object_storage" { value = "${azurerm_storage_account.this.name}/${azurerm_storage_container.data.name}" }
output "resource_group" { value = azurerm_resource_group.this.name }
output "cluster_name"   { value = azurerm_kubernetes_cluster.this.name }
