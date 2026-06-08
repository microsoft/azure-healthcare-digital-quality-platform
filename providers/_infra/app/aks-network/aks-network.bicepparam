using './nsg-http-rules.bicep'

// NIC-level NSG (in node resource group) — found via:
//   az network nsg list --resource-group mc_aks-pynargp3zuafw_eastus2 --query "[].name" -o tsv
// Deploy to node resource group:
//   az deployment group create --resource-group mc_aks-pynargp3zuafw_eastus2 \
//     --template-file nsg-http-rules.bicep --parameters aks-network.bicepparam

param nsgName = 'aks-agentpool-11491980-nsg'
param httpRulePriority = 100
param httpsRulePriority = 101
