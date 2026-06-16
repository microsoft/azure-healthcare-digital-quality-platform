variable "namespace"    { type = string }
variable "stack"        { type = string }
variable "image_tag"    { type = string }
variable "registry_url" { type = string }
variable "labels"       { type = map(string) }

variable "services" {
  type = map(object({
    dockerfile = string
    port       = number
    replicas   = optional(number, 1)
  }))
}

resource "kubernetes_namespace" "this" {
  metadata {
    name   = var.namespace
    labels = var.labels
  }
}

resource "kubernetes_deployment" "svc" {
  for_each = var.services

  metadata {
    name      = each.key
    namespace = kubernetes_namespace.this.metadata[0].name
    labels    = merge(var.labels, { app = each.key, stack = var.stack })
  }

  spec {
    replicas = each.value.replicas
    selector { match_labels = { app = each.key } }

    template {
      metadata { labels = merge(var.labels, { app = each.key, stack = var.stack }) }
      spec {
        container {
          name  = each.key
          image = "${var.registry_url}/${each.key}:${var.image_tag}"

          port { container_port = each.value.port }

          readiness_probe {
            http_get {
              path = each.key == "frontend" ? "/" : "/health"
              port = each.value.port
            }
            initial_delay_seconds = 10
            period_seconds        = 10
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "svc" {
  for_each = var.services

  metadata {
    name      = each.key
    namespace = kubernetes_namespace.this.metadata[0].name
    labels    = merge(var.labels, { app = each.key })
  }

  spec {
    selector = { app = each.key }
    port {
      port        = each.value.port
      target_port = each.value.port
    }
    type = each.key == "frontend" ? "LoadBalancer" : "ClusterIP"
  }
}

output "service_endpoints" {
  value = {
    for k, _ in var.services :
    k => "${k}.${var.namespace}.svc.cluster.local"
  }
}
