# Bring-your-own Kubernetes target. No infrastructure is provisioned; we
# pass the user's existing kubeconfig and registry through to downstream
# modules.

variable "kubeconfig_path"    { type = string }
variable "kubeconfig_context" { type = string }
variable "registry"           { type = string }

output "cluster" {
  value = {
    host                   = null
    ca_certificate         = null
    token                  = null
    client_certificate     = null
    client_key             = null
    config_path            = pathexpand(var.kubeconfig_path)
    config_context         = var.kubeconfig_context != "" ? var.kubeconfig_context : null
  }
}

output "registry_url"   { value = var.registry }
output "object_storage" { value = null }
output "cluster_name"   { value = "byo" }
