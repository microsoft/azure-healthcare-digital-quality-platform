variable "target_platform" {
  description = "Where to deploy the stack. One of: azure | aws | gcp | kubernetes | docker."
  type        = string
  validation {
    condition     = contains(["azure", "aws", "gcp", "kubernetes", "docker"], var.target_platform)
    error_message = "target_platform must be one of azure, aws, gcp, kubernetes, docker."
  }
}

variable "stack" {
  description = "Which platform stack to deploy (consumers, providers, submitters, receivers, platform)."
  type        = string
  default     = "submitters"
  validation {
    condition     = contains(["consumers", "providers", "submitters", "receivers", "platform"], var.stack)
    error_message = "stack must be one of consumers, providers, submitters, receivers, platform."
  }
}

variable "environment" {
  description = "Environment short name (e.g. dev, test, prod). Used in resource naming."
  type        = string
  default     = "dev"
}

variable "name_prefix" {
  description = "Prefix applied to provisioned resources."
  type        = string
  default     = "ahdqp"
}

variable "region" {
  description = "Cloud region (Azure location / AWS region / GCP region). Ignored for kubernetes/docker."
  type        = string
  default     = ""
}

variable "image_tag" {
  description = "Container image tag to deploy for all services."
  type        = string
  default     = "latest"
}

variable "services" {
  description = "Service name -> container source path (relative to repo root) and exposed port."
  type = map(object({
    dockerfile = string
    port       = number
    replicas   = optional(number, 1)
  }))
  default = {
    backend = {
      dockerfile = "backend/Dockerfile"
      port       = 5000
    }
    frontend = {
      dockerfile = "frontend/Dockerfile"
      port       = 80
    }
    orchestrator = {
      dockerfile = "orchestrator/Dockerfile"
      port       = 8000
    }
  }
}

variable "namespace" {
  description = "Kubernetes namespace the stack is deployed into."
  type        = string
  default     = "dq"
}

# Cloud-specific overrides --------------------------------------------------

variable "azure" {
  description = "Azure-specific options."
  type = object({
    subscription_id = optional(string, "")
    resource_group  = optional(string, "")
    aks_node_size   = optional(string, "Standard_D2s_v5")
    aks_node_count  = optional(number, 2)
  })
  default = {}
}

variable "aws" {
  description = "AWS-specific options."
  type = object({
    eks_version    = optional(string, "1.30")
    node_size      = optional(string, "t3.large")
    node_count     = optional(number, 2)
    vpc_cidr       = optional(string, "10.20.0.0/16")
  })
  default = {}
}

variable "gcp" {
  description = "GCP-specific options."
  type = object({
    project_id  = optional(string, "")
    node_size   = optional(string, "e2-standard-2")
    node_count  = optional(number, 2)
  })
  default = {}
}

variable "kubernetes_byo" {
  description = "Bring-your-own Kubernetes cluster options (used when target_platform = kubernetes)."
  type = object({
    kubeconfig_path    = optional(string, "~/.kube/config")
    kubeconfig_context = optional(string, "")
    registry           = optional(string, "")
  })
  default = {}
}

variable "docker_local" {
  description = "Local docker options (used when target_platform = docker)."
  type = object({
    # Default resolves to the repo root from deploy/terraform/.
    build_context = optional(string, "../..")
    host_port_map = optional(map(number), {
      backend      = 8000
      frontend     = 5173
      orchestrator = 8001
    })
  })
  default = {}
}
