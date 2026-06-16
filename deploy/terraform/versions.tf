terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm    = { source = "hashicorp/azurerm",    version = "~> 4.0" }
    aws        = { source = "hashicorp/aws",        version = "~> 5.60" }
    google     = { source = "hashicorp/google",     version = "~> 6.0" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.30" }
    helm       = { source = "hashicorp/helm",       version = "~> 2.14" }
    docker     = { source = "kreuzwerker/docker",   version = "~> 3.0" }
    random     = { source = "hashicorp/random",     version = "~> 3.6" }
  }
}
