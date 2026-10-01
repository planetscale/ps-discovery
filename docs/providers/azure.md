# Azure Cloud Discovery Setup

## Overview

The PlanetScale Discovery CLI can analyze Azure Database for PostgreSQL Flexible
Servers, Azure Database for MySQL Flexible Servers, and the virtual network
infrastructure around them.

It collects metadata only — server inventory, compute size, storage, backup and
high-availability posture, network exposure, and customized server parameters.
It never reads your data.

## Prerequisites

- An Azure subscription with Flexible Servers in it
- A service principal or a signed-in Azure CLI session with the **Reader** role
- The Azure dependencies installed. From the extracted release directory,
  either run `./setup.sh` and pick **Azure** at the provider prompt, or install
  the extra directly:

  ```bash
  pip install -e ".[azure]"
  ```

  This installs `azure-identity`, `azure-mgmt-postgresqlflexibleservers`,
  `azure-mgmt-mysqlflexibleservers` and `azure-mgmt-network`.

## Required Resource Providers

Azure only serves a resource type once its provider is registered on the
subscription. Check what you have:

```bash
az provider show -n Microsoft.DBforPostgreSQL --query registrationState -o tsv
az provider show -n Microsoft.DBforMySQL     --query registrationState -o tsv
az provider show -n Microsoft.Network        --query registrationState -o tsv
```

Register anything that comes back as `NotRegistered`:

```bash
az provider register --namespace Microsoft.DBforPostgreSQL
az provider register --namespace Microsoft.DBforMySQL
az provider register --namespace Microsoft.Network
```

`az provider register` needs write access, which the Reader role does not
grant — so a subscription owner has to run it, not the account doing the
discovery.

You do **not** need to register a provider you do not use. A subscription with
only MySQL servers will report `Microsoft.DBforPostgreSQL` as unregistered, and
discovery treats that as a warning and carries on.

## Required Azure Permissions

Use the built-in **Reader** role at subscription scope. It is sufficient. It
grants `*/read` and nothing else, so it cannot modify anything.

Reader never exposes credentials: the management API does not return
`administrator_login_password` at all, and this tool collects no login or
password fields regardless.

If your organization will not grant Reader, create a custom role with exactly
these actions:

```json
{
  "Name": "PlanetScale Discovery Reader",
  "IsCustom": true,
  "Description": "Read-only discovery of Azure database and network resources",
  "Actions": [
    "Microsoft.Resources/subscriptions/read",
    "Microsoft.Resources/subscriptions/resourceGroups/read",

    "Microsoft.DBforPostgreSQL/flexibleServers/read",
    "Microsoft.DBforPostgreSQL/flexibleServers/configurations/read",
    "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules/read",

    "Microsoft.DBforMySQL/flexibleServers/read",
    "Microsoft.DBforMySQL/flexibleServers/configurations/read",
    "Microsoft.DBforMySQL/flexibleServers/firewallRules/read",

    "Microsoft.Network/virtualNetworks/read",
    "Microsoft.Network/virtualNetworks/subnets/read",
    "Microsoft.Network/networkSecurityGroups/read"
  ],
  "NotActions": [],
  "DataActions": [],
  "AssignableScopes": ["/subscriptions/<subscription-id>"]
}
```

Create it with:

```bash
az role definition create --role-definition ./planetscale-discovery-role.json
```

Wildcards work too, if your security team prefers them:
`Microsoft.DBforPostgreSQL/*/read`, `Microsoft.DBforMySQL/*/read`,
`Microsoft.Network/*/read`.

## Authentication Options

### Option 1: Service Principal (Recommended)

```bash
az ad sp create-for-rbac \
  --name "planetscale-discovery" \
  --role Reader \
  --scopes "/subscriptions/<subscription-id>"
```

**Pass `--role Reader` explicitly.** Without it the service principal gets no
role assignment at all on current Azure CLI (2.7x and later), so discovery
authenticates and then finds nothing; older CLI versions defaulted to
**Contributor**, which grants write access this tool has no use for. Either way,
naming the role is what you want.

Verify the assignment landed:

```bash
az role assignment list --assignee <appId> --output table
```

The command prints three values:

```json
{
  "appId": "00000000-0000-0000-0000-000000000000",
  "displayName": "planetscale-discovery",
  "password": "EXAMPLE~secret~value~replace~me",
  "tenant": "11111111-1111-1111-1111-111111111111"
}
```

**Azure and this tool use different names for the same three values.** There is
no second command to run — just rename them:

| `az` output | Config field | Also settable as |
| --- | --- | --- |
| `appId` | `client_id` | `AZURE_CLIENT_ID` |
| `password` | `client_secret` | `AZURE_CLIENT_SECRET` |
| `tenant` | `tenant_id` | `AZURE_TENANT_ID` |
| *(not in the output)* | `subscription_id` | `AZURE_SUBSCRIPTION_ID` |

`displayName` is not used. **`password` is shown only once** and cannot be
retrieved afterwards — if you lose it, run
`az ad sp credential reset --id <appId>` to issue a new one.

`subscription_id` is the fourth value you need and it is *not* in the output
above. Get it with:

```bash
az account show --query id -o tsv
```

Putting those four together, using the example output above:

```yaml
providers:
  azure:
    enabled: true
    # from: az account show --query id -o tsv
    subscription_id: 22222222-2222-2222-2222-222222222222
    credentials:
      tenant_id: 11111111-1111-1111-1111-111111111111      # "tenant"
      client_id: 00000000-0000-0000-0000-000000000000      # "appId"
      client_secret: EXAMPLE~secret~value~replace~me   # "password"
```

The values above are placeholders. Use your own, protect the file
(`chmod 600`), and keep it out of source control.

Then run:

```bash
ps-discovery cloud --config config.yaml
```

### Option 2: Azure CLI Sign-In

Omit the credentials block entirely and the tool falls back to
`DefaultAzureCredential`, which picks up your `az login` session, a managed
identity when running on an Azure VM, or the `AZURE_*` environment variables —
in that order.

```bash
az login
az account set --subscription "<subscription-id>"
```

```yaml
providers:
  azure:
    enabled: true
    subscription_id: 22222222-2222-2222-2222-222222222222
```

This is the quickest way to try the tool, and the right choice when running
discovery from inside Azure with a managed identity.

### Option 3: Environment Variables

These are the names the Azure SDK reads natively, so setting them works whether
or not you also list credentials in the config file:

```bash
export AZURE_ENABLED=true
export AZURE_SUBSCRIPTION_ID=22222222-2222-2222-2222-222222222222
export AZURE_TENANT_ID=11111111-1111-1111-1111-111111111111
export AZURE_CLIENT_ID=00000000-0000-0000-0000-000000000000
export AZURE_CLIENT_SECRET=your-client-secret

# Optional
export AZURE_REGIONS=eastus,westeurope
export AZURE_RESOURCE_GROUPS=rg-prod,rg-staging
```

There is deliberately **no `--azure-client-secret` command-line flag**. A secret
on the command line ends up in your shell history and in the output of `ps`, so
the config file and the environment variables are the only supported paths.

## Configuration Examples

### Basic: the whole subscription

```yaml
providers:
  azure:
    enabled: true
    subscription_id: 22222222-2222-2222-2222-222222222222
    credentials:
      tenant_id: 11111111-1111-1111-1111-111111111111
      client_id: 00000000-0000-0000-0000-000000000000
      client_secret: your-client-secret
    discover_all: true
```

### Scoped to specific resource groups

Useful when your service principal is scoped to a resource group rather than
the whole subscription — see the note under Regional Considerations.

```yaml
providers:
  azure:
    enabled: true
    subscription_id: 22222222-2222-2222-2222-222222222222
    resource_groups:
      - rg-prod-db
      - rg-staging-db
```

### Scoped to specific regions

```yaml
providers:
  azure:
    enabled: true
    subscription_id: 22222222-2222-2222-2222-222222222222
    regions:
      - eastus
      - westeurope
```

### Scoped to named servers

```yaml
providers:
  azure:
    enabled: true
    subscription_id: 22222222-2222-2222-2222-222222222222
    discover_all: false
    resources:
      postgresql_flexible_servers:
        - pg-prod-01
        - pg-prod-02
      mysql_flexible_servers:
        - mysql-reporting
```

### Command line

```bash
# Whole subscription
ps-discovery cloud --providers azure --azure-subscription <subscription-id>

# Narrow to regions or resource groups
ps-discovery cloud --providers azure \
  --azure-subscription <subscription-id> \
  --regions eastus,westeurope

ps-discovery cloud --providers azure \
  --azure-subscription <subscription-id> \
  --azure-resource-groups rg-prod-db,rg-staging-db

# Alongside other providers
ps-discovery cloud --providers aws,azure
```

## Regional Considerations

**Azure lists resources per subscription, not per region.** Three consequences
worth knowing:

1. **`regions` is a filter, not a loop.** The tool makes one subscription-wide
   call per resource type and then keeps the resources whose own location
   matches your filter.

2. **An empty or omitted `regions` list means *every* region.** There is no
   default region: a subscription-wide listing costs the same filtered or not,
   and picking one for you would silently hide servers.

3. **Region names are matched loosely.** `eastus`, `East US` and `east us` all
   work — case and spaces are normalized on both sides before comparison.

**Get the region names from your own servers rather than copying the examples
here.** Azure names often end in a digit, and `eastus` and `eastus2` are
different regions:

```bash
az postgres flexible-server list --query "[].{name:name,region:location}" -o table
az mysql flexible-server list --query "[].{name:name,region:location}" -o table
```

If a filter matches nothing, discovery says so and names the regions your
resources are actually in, rather than reporting an empty result:

```
WARNING - The configured regions matched no resources at all.
Configured: eastus, westeurope. Skipped by region filter: eastus2 (3), westus2 (1).
```

Output is keyed by region:

```json
"azure": {
  "subscription_id": "2222...",
  "regions_analyzed": ["eastus", "westeurope"],
  "resources": {
    "eastus": {
      "postgresql_flexible_servers": [...],
      "mysql_flexible_servers": [...],
      "virtual_networks": [...],
      "network_security_groups": [...]
    }
  }
}
```

When you set `regions`, every region you asked for appears in the output even if
it turned out to be empty, so you can tell "nothing there" apart from "never
looked". When you do not set `regions`, only regions that actually contain
resources appear.

**`resource_groups` scopes everything**, servers and networking alike. Without
it the tool lists the whole subscription, which means virtual networks and
security groups belonging to other teams appear in your report and their regions
show up in `regions_analyzed`. Set `resource_groups` when you only want your
own.

**If your service principal is scoped to a resource group** rather than the
subscription, the subscription-wide calls return empty or 403. Set
`resource_groups` to match the scope you were granted; the tool then issues one
scoped call per group instead.

## Data Collected

### Flexible Servers (PostgreSQL and MySQL)

- Name, location, resource group, state, fully qualified domain name
- Engine version and minor version
- Compute SKU name and tier (for example `Standard_D4ds_v5`, `GeneralPurpose`)
- `compute_specs`: vCPU, RAM, provisioned storage and CPU architecture.
  Azure states compute only as a SKU name, so vCPU and RAM are resolved
  from a table generated from Azure's own capability API. An unrecognised
  SKU omits them and logs a warning rather than guessing.
- Storage: provisioned size, tier, IOPS, auto-grow; plus auto-IO-scaling and
  storage redundancy on MySQL
- Backup: retention days, geo-redundancy
- High availability: mode, state, standby availability zone
- Network: public network access, delegated subnet (VNet injection), private
  DNS zone
- Availability zone, replication role, replica capacity, source server
- Data encryption type (service-managed or customer-managed key). MySQL
  Flexible Server returns this only when a customer-managed key is configured,
  so the field is empty on a service-managed server. Azure encrypts storage at
  rest either way, which is why `summary.encrypted_servers` counts every
  server.
- Resource tags
- Engine version, including a combined `version_full` (PostgreSQL reports
  `version: "16"` with `minor_version: "15"`, meaning 16.15; MySQL reports
  `version: "8.0.21"` already complete, so `version_full` is comparable across
  both)
- Server parameters that differ from the service default, each flagged
  `is_read_only` — see the note below
- Server-level firewall rules, with permissive rules flagged

#### A note on server parameters

Azure marks **its own platform configuration** as `user-override`, not
`system-default`. On a stock PostgreSQL 16 Flexible Server that is around 36
settings — `config_file`, `data_directory`, `ssl_cert_file`, `server_version`,
`shared_memory_size`, `archive_command`, `listen_addresses` and similar — none
of which anyone on your team chose.

So `server_parameters` reports every non-default parameter as raw data, and
flags each one:

- `is_read_only: true` — Azure's own platform setting. Not a migration decision.
- `is_read_only: false` — genuinely tunable, and something someone set.

`complexity_factors.custom_server_parameters` counts **only the writable ones**.
On the test server above that is 2 out of 38 for PostgreSQL and 3 out of 7 for
MySQL. If you compare against the Azure portal, note that the portal shows the
same `user-override` source without separating the two.

### Networking

- Virtual networks: address prefixes, tags
- Subnets: address prefixes, delegations, service endpoints, associated NSG
- Virtual network peerings: state, remote network, traffic settings
- Network security groups and their custom rules (direction, access, protocol,
  priority, source prefixes, destination ports), with internet-exposed rules
  flagged

Azure's built-in default NSG rules are **not** collected — only rules your team
added.

### Output shape

Each region in the report carries the Azure-native arrays
(`postgresql_flexible_servers`, `mysql_flexible_servers`, `virtual_networks`,
`network_security_groups`) **and** four provider-neutral ones derived from them
(`instances`, `vpcs`, `security_groups`, `db_subnet_groups`), so that tools
which expect a provider-neutral vocabulary can read an Azure report without
translation. `instances` is the only instance key — nothing duplicates that
list under a second name. Nothing is double-counted: `summary` counts only the
Azure-native arrays.

| Array | Derived from | Fields |
| --- | --- | --- |
| `instances[]` | both `*_flexible_servers` arrays | `db_instance_identifier`, `db_instance_class`, `engine`, `engine_version`, `cpu_cores`, `memory_gb`, `allocated_storage`, `multi_az`, `iops`, `publicly_accessible` |
| `vpcs[]` | `virtual_networks` | `vpc_id`, `cidr_block`, `subnets[]` |
| `security_groups[]` | `network_security_groups` | `group_id`, `group_name`, `security_rules[]` |
| `db_subnet_groups[]` | servers with VNet injection | `name`, `vpc_id`, `subnets[]` |

Fields that only Azure has are under one `_azure` key on each instance.
`_ai_extracted` is always `false`, because the values come from the Azure API.

The two server arrays carry the same compute values under different names:

| Location | Keys |
| --- | --- |
| `*_flexible_servers[].compute_specs` | `vcpu`, `ram_gb`, `storage_gb`, `architecture` |
| `instances[]` | `cpu_cores`, `memory_gb`, `allocated_storage`, `architecture` |

`cpu_cores` and `memory_gb` on a neutral instance are resolved from a SKU table
generated from Azure's own capability API. If Azure adds a compute SKU the table
does not know, discovery warns and omits those two fields rather than guessing;
regenerate the table as described at the top of
`planetscale_discovery/cloud/analyzers/azure_sku_specs.py`.

## Limitations

Deliberately out of scope in this release:

| Not collected | Why |
| --- | --- |
| **Azure Cosmos DB for PostgreSQL** (Citus) | Planned as a follow-up. It uses a separate SDK and a different cluster/node model. |
| **Actual storage *used*** | Only *provisioned* storage size is collected. Live usage would require Azure Monitor, an additional permission, and one metrics call per server. |
| **Private endpoints** | Flexible Server uses VNet injection or public access with firewall rules, both of which are collected. |
| **Database lists per server** | The database module already enumerates databases on the server it connects to. |
| **Route tables, NAT gateways** | VNet, subnet and NSG data already answers what is reachable and what guards it. |
| **Single Server** (PostgreSQL/MySQL) | Retired by Azure. |

Azure Database for PostgreSQL and MySQL **Single Server** deployments are not
discovered. If you still run them, tell your PlanetScale contact.

## Troubleshooting

### `SubscriptionNotFound`

**Problem:** the subscription ID is wrong, or the credential belongs to a
different tenant.

**Solutions:**
- Confirm the ID with `az account list --output table`
- Check that the service principal was created in the same tenant as the
  subscription
- Make sure you passed the *subscription* ID, not the tenant or client ID

### `AuthorizationFailed`

**Problem:** the credential authenticated but is not authorized to read.

**Solutions:**
- Verify the role assignment:
  `az role assignment list --assignee <client-id> --output table`
- Assign Reader:
  `az role assignment create --assignee <client-id> --role Reader --scope /subscriptions/<subscription-id>`
- If the assignment is scoped to a resource group, set `resource_groups` in your
  config to match

### `MissingSubscriptionRegistration`

**Problem:** the resource provider is not registered on the subscription.

**Solutions:**
- This is reported as a **warning** and discovery continues, so you can ignore
  it for an engine you do not use
- To fix it, have a subscription owner run
  `az provider register --namespace Microsoft.DBforPostgreSQL` (or
  `Microsoft.DBforMySQL`)

### `DefaultAzureCredential failed to retrieve a token`

**Problem:** no ambient credential was found.

**Solutions:**
- Run `az login`
- Or supply a service principal via the config file or the `AZURE_*`
  environment variables
- On an Azure VM, confirm a managed identity is assigned to the VM

### `InvalidAuthenticationTokenTenant`

**Problem:** the service principal's home tenant is not the subscription's
tenant.

**Solutions:**
- Set `tenant_id` to the tenant that owns the **subscription**
- Find it with
  `az account show --query tenantId -o tsv`

### Discovery returns no servers

**Problem:** the run succeeds but finds nothing.

**Solutions:**
- Check whether `regions` is filtering everything out — remove it to scan every
  region
- Check whether `discover_all: false` is set without a matching `resources`
  list, which skips discovery and logs a warning
- Confirm the credential can see the servers:
  `az postgres flexible-server list --output table`

### Azure dependencies not installed

**Problem:** the log says
`Azure dependencies not installed.`

**Solution:** install the extra with `pip install -e ".[azure]"` from the
release directory, or re-run `./setup.sh` and pick Azure. The Azure SDK is
optional so that users of other providers do not pay for it.

## Security Best Practices

1. **Use the Reader role.** Never grant Contributor for discovery. Remember that
   `az ad sp create-for-rbac` defaults to Contributor if you omit `--role`.
2. **Prefer `az login` or a managed identity** over a long-lived client secret
   where you can.
3. **Keep secrets out of the command line.** Use the config file or the `AZURE_*`
   environment variables; there is no flag for the client secret.
4. **Protect the config file:** `chmod 600 config.yaml`.
5. **Scope down if you can.** A resource-group-scoped role assignment plus
   `resource_groups` in the config works fine.
6. **Delete the service principal when the assessment is done:**
   `az ad sp delete --id <client-id>`.
7. **Output files are written `0600`** and contain no credentials, but they do
   describe your infrastructure — handle them accordingly.

## Recommendations

The discovery output is meant to inform these questions:

- **High availability:** servers with HA mode `Disabled` have no standby. The
  report counts zone-redundant and same-zone deployments separately from
  single-instance ones.
- **Backups:** Flexible Server always has backups, so the report tracks
  *geo-redundant* backups, which is the signal that matters for regional
  failure.
- **Network exposure:** a server with `public_network_access: Enabled` and a
  firewall rule of `0.0.0.0`–`255.255.255.255` is reachable from anywhere. The
  report flags those rules as `allows_all_addresses`.
- **"Allow all Azure services":** a firewall rule of `0.0.0.0`–`0.0.0.0` is
  Azure's sentinel for this, and it admits traffic from other tenants'
  subscriptions, not just yours. Flagged as `allows_azure_services`.
- **Customized server parameters:** a *writable* non-default parameter
  (`is_read_only: false`) is a setting that has to be reproduced or consciously
  dropped during a migration. Ignore the read-only ones — those are Azure's own
  platform configuration, not a choice your team made.
- **Read replicas:** counted from each server's replication role. A replica whose
  source sits in another region is also counted as a cross-region replica.

## Additional Resources

- [Azure Database for PostgreSQL Flexible Server](https://learn.microsoft.com/azure/postgresql/flexible-server/)
- [Azure Database for MySQL Flexible Server](https://learn.microsoft.com/azure/mysql/flexible-server/)
- [Azure RBAC built-in roles](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles)
- [Azure Virtual Network documentation](https://learn.microsoft.com/azure/virtual-network/)
- [DefaultAzureCredential](https://learn.microsoft.com/python/api/overview/azure/identity-readme#defaultazurecredential)

## Support

For issues with Azure discovery:
- Report bugs: https://github.com/planetscale/ps-discovery/issues
- Azure Support: https://azure.microsoft.com/support/
