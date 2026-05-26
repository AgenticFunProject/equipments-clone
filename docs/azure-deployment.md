# Azure Deployment

This repository deploys to Azure Container Apps using Azure Container Registry
and Log Analytics. The GitHub Actions deployment workflow is manual by default
and uses Azure CLI plus OpenID Connect.

## Architecture

- Azure Container Registry stores the built Docker image.
- Azure Container Apps runs the FastAPI service with external HTTPS ingress.
- Log Analytics receives Container Apps logs.
- `/health` is configured as the readiness and liveness probe.
- The registry template enables ACR admin credentials so the workflow can push
  and Container Apps can pull images without requiring the deployment principal
  to manage Azure role assignments.
- Runtime storage is `memory` for this deployment template. The clone supports
  SQLite locally, but Container Apps replicas do not provide durable shared
  SQLite storage without adding a volume design.

## One-Time Azure Setup

Set the working values:

```bash
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
RESOURCE_GROUP=equipments-clone-rg
LOCATION=northeurope
APP_NAME=equipments-clone
REPO=AgenticFunProject/equipments-clone
ENVIRONMENT=azure-production
```

Register providers:

```bash
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.ContainerRegistry --wait
az provider register --namespace Microsoft.OperationalInsights --wait
```

Create the resource group:

```bash
az group create --name "$RESOURCE_GROUP" --location "$LOCATION"
```

Create a Microsoft Entra application for GitHub Actions:

```bash
APP_ID=$(az ad app create \
  --display-name "equipments-clone-github-actions" \
  --query appId \
  --output tsv)

az ad sp create --id "$APP_ID"

az role assignment create \
  --assignee "$APP_ID" \
  --role Contributor \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP"
```

Create the GitHub OIDC federated credential. The workflow uses the
`azure-production` environment, so the federated subject is environment-scoped:

```bash
cat > /tmp/equipments-clone-federated-credential.json <<EOF
{
  "name": "equipments-clone-azure-production",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:${REPO}:environment:${ENVIRONMENT}",
  "description": "GitHub Actions deployment for equipments-clone",
  "audiences": ["api://AzureADTokenExchange"]
}
EOF

az ad app federated-credential create \
  --id "$APP_ID" \
  --parameters @/tmp/equipments-clone-federated-credential.json
```

Create the GitHub environment and secrets:

```bash
gh api \
  --method PUT \
  "repos/${REPO}/environments/${ENVIRONMENT}"

gh secret set AZURE_CLIENT_ID --repo "$REPO" --body "$APP_ID"
gh secret set AZURE_TENANT_ID --repo "$REPO" --body "$TENANT_ID"
gh secret set AZURE_SUBSCRIPTION_ID --repo "$REPO" --body "$SUBSCRIPTION_ID"
AUTH_JWT_SECRET="$(openssl rand -base64 48)"
gh secret set AUTH_JWT_SECRET --repo "$REPO" --body "$AUTH_JWT_SECRET"
```

Use your platform-issued HS256 secret instead if callers already mint service
JWTs.

## Deploy

Run the manual workflow:

```bash
gh workflow run deploy-azure.yml \
  --repo "$REPO" \
  -f resourceGroup="$RESOURCE_GROUP" \
  -f location="$LOCATION" \
  -f appName="$APP_NAME"
```

Watch it:

```bash
gh run watch --repo "$REPO" --exit-status
```

The workflow:

1. Logs in to Azure with OIDC.
2. Creates or updates Azure Container Registry.
3. Builds and pushes the Docker image.
4. Creates or updates the Container App.
5. Smoke-tests `https://<fqdn>/health`.

## Manual Azure CLI Deployment

You can run the same deployment locally after `az login`:

```bash
az group create --name "$RESOURCE_GROUP" --location "$LOCATION"

ACR_OUTPUTS=$(az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file infra/registry.bicep \
  --parameters location="$LOCATION" appName="$APP_NAME" \
  --query properties.outputs)

ACR_NAME=$(echo "$ACR_OUTPUTS" | jq -r '.acrName.value')
LOGIN_SERVER=$(echo "$ACR_OUTPUTS" | jq -r '.loginServer.value')
IMAGE="${LOGIN_SERVER}/${APP_NAME}:manual"

ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query 'passwords[0].value' -o tsv)

echo "$ACR_PASSWORD" | docker login "$LOGIN_SERVER" \
  --username "$ACR_USERNAME" \
  --password-stdin

docker build -t "$IMAGE" .
docker push "$IMAGE"

az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file infra/container-app.bicep \
  --parameters \
      location="$LOCATION" \
      appName="$APP_NAME" \
      acrName="$ACR_NAME" \
      image="$IMAGE" \
      authJwtSecret="$AUTH_JWT_SECRET"
```

Get the URL:

```bash
FQDN=$(az containerapp show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$APP_NAME" \
  --query properties.configuration.ingress.fqdn \
  --output tsv)

curl -fsS "https://${FQDN}/health"
```
