targetScope = 'resourceGroup'

@description('Azure region for Container Apps resources.')
param location string = resourceGroup().location

@description('Container App name.')
param appName string = 'equipments-clone'

@description('Existing Azure Container Registry name.')
param acrName string

@description('Fully qualified container image, including registry and tag.')
param image string

@secure()
@description('HS256 JWT signing secret shared with callers that mint service tokens.')
param authJwtSecret string

@description('Expected JWT issuer.')
param authJwtIssuer string = 'platform-auth'

@description('Expected JWT audience.')
param authJwtAudience string = 'equipments-service'

@allowed([
  'development'
  'production'
])
@description('Application environment. Production disables development-only routes.')
param appEnv string = 'production'

@allowed([
  'memory'
])
@description('Runtime storage backend for the Azure deployment.')
param storageBackend string = 'memory'

@minValue(0)
@description('Minimum Container Apps replica count.')
param minReplicas int = 1

@minValue(1)
@description('Maximum Container Apps replica count.')
param maxReplicas int = 3

@description('Container CPU cores.')
param cpu string = '0.5'

@description('Container memory.')
param memory string = '1Gi'

var logAnalyticsName = '${appName}-logs'
var environmentName = '${appName}-env'

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource containerEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var registryCredentials = registry.listCredentials()

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  properties: {
    managedEnvironmentId: containerEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 3000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registry.properties.loginServer
          username: registryCredentials.username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: registryCredentials.passwords[0].value
        }
        {
          name: 'auth-jwt-secret'
          value: authJwtSecret
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'equipments-clone'
          image: image
          env: [
            {
              name: 'APP_ENV'
              value: appEnv
            }
            {
              name: 'AUTH_JWT_ISSUER'
              value: authJwtIssuer
            }
            {
              name: 'AUTH_JWT_AUDIENCE'
              value: authJwtAudience
            }
            {
              name: 'AUTH_JWT_SECRET'
              secretRef: 'auth-jwt-secret'
            }
            {
              name: 'STORAGE_BACKEND'
              value: storageBackend
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 3000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 3000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
          resources: {
            cpu: json(cpu)
            memory: memory
          }
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

output appName string = app.name
output fqdn string = app.properties.configuration.ingress.fqdn
output containerAppEnvironment string = containerEnvironment.name
