variable "name"          { type = string }
variable "stack"         { type = string }
variable "image_tag"     { type = string }
variable "build_context" { type = string }
variable "host_port_map" { type = map(number) }

variable "services" {
  type = map(object({
    dockerfile = string
    port       = number
    replicas   = optional(number, 1)
  }))
}

resource "docker_network" "dq" {
  name = "${var.name}-net"
}

resource "docker_image" "svc" {
  for_each = var.services
  name     = "${var.name}-${each.key}:${var.image_tag}"

  build {
    context    = var.build_context
    dockerfile = "${var.stack}/${each.value.dockerfile}"
  }
}

resource "docker_container" "svc" {
  for_each = var.services
  name     = "${var.name}-${each.key}"
  image    = docker_image.svc[each.key].image_id
  restart  = "unless-stopped"

  networks_advanced {
    name    = docker_network.dq.name
    aliases = each.key == "backend" ? [each.key, "backend.dq.svc.cluster.local"] : [each.key]
  }

  ports {
    internal = each.value.port
    external = lookup(var.host_port_map, each.key, each.value.port)
  }
}

output "endpoints" {
  value = {
    for k, v in var.services :
    k => "http://localhost:${lookup(var.host_port_map, k, v.port)}"
  }
}
