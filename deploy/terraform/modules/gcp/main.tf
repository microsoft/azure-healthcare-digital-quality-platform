variable "name"       { type = string }
variable "region"     { type = string }
variable "project_id" { type = string }
variable "node_size"  { type = string }
variable "node_count" { type = number }
variable "labels"     { type = map(string) }

resource "google_artifact_registry_repository" "this" {
  location      = var.region
  repository_id = "${var.name}-images"
  format        = "DOCKER"
  labels        = var.labels
}

resource "google_storage_bucket" "data" {
  name                        = "${var.name}-data"
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
  labels                      = var.labels
}

resource "google_container_cluster" "this" {
  name                     = "${var.name}-gke"
  location                 = var.region
  remove_default_node_pool = true
  initial_node_count       = 1
  deletion_protection      = false
  resource_labels          = var.labels
}

resource "google_container_node_pool" "default" {
  name       = "default"
  cluster    = google_container_cluster.this.name
  location   = var.region
  node_count = var.node_count

  node_config {
    machine_type = var.node_size
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    labels       = var.labels
  }
}

data "google_client_config" "current" {}

output "cluster" {
  value = {
    host                   = "https://${google_container_cluster.this.endpoint}"
    ca_certificate         = base64decode(google_container_cluster.this.master_auth[0].cluster_ca_certificate)
    token                  = data.google_client_config.current.access_token
    client_certificate     = null
    client_key             = null
    config_path            = null
    config_context         = null
  }
  sensitive = true
}

output "registry_url" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.this.repository_id}"
}

output "object_storage" { value = google_storage_bucket.data.name }
output "cluster_name"   { value = google_container_cluster.this.name }
