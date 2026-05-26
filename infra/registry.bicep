targetScope = 'resourceGroup'

@description('Azure region for the container registry.')
param location string = resourceGroup().location

@description('Application name used to derive resource names.')
param appName string = 'equipments-clone'

@allowed([
  'Basic'
  'Standard'
  'Premium'
])
@description('Azure Container Registry SKU.')
param acrSku string = 'Basic'

var registryPrefix = take(toLower(replace(replace(appName, '-', ''), '_', '')), 30)
var registryName = '${registryPrefix}${uniqueString(resourceGroup().id, appName)}'

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: {
    name: acrSku
  }
  properties: {
    adminUserEnabled: true
  }
}

output acrName string = registry.name
output loginServer string = registry.properties.loginServer
