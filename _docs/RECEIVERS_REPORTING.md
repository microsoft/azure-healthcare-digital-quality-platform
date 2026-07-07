# Receivers Reporting: Azure SQL Database and Power BI

The Receivers stack provisions an Azure SQL Database for operational reporting and includes a source-controlled Power BI Project template at `receivers/powerbi/ReceiverAnalytics.pbip`.

## Provisioned resources

`receivers/_infra/main.bicep` adds:

- Azure SQL logical server (`AZURE_SQL_SERVER_NAME`, `AZURE_SQL_SERVER_FQDN`)
- Azure SQL Database (`AZURE_SQL_DATABASE_NAME`, default `dq_receiver_reporting`)
- Private endpoint and `privatelink.database.windows.net` DNS zone when `vnetEnabled=true`
- Optional Microsoft Entra SQL administrator (`AZURE_SQL_ENTRA_ADMIN_OBJECT_ID`, `AZURE_SQL_ENTRA_ADMIN_LOGIN`)
- Managed-identity ODBC connection string output (`AZURE_SQL_CONNECTION_STRING`)

The post-provision hooks write these values to `receivers/backend/.env` and set `RECEIVER_REPORTING_SQL_ENABLED=true`.

## Database schema

Apply `receivers/reporting/sql/schema.sql` after `azd provision` completes. It creates schema `dq` and these tables:

| Area | Table |
|---|---|
| Programs | `dq.Programs` |
| Measures | `dq.Measures` |
| Submitters | `dq.Submitters` |
| Cohorts | `dq.Cohorts` |
| Submission history | `dq.SubmissionHistory` |
| Measure reports | `dq.MeasureReports` |
| Processing events | `dq.ProcessingEvents` |
| Audit logs | `dq.AuditLogs` |
| Quality metrics | `dq.QualityMetrics` |

For managed identity access, connect as the configured Microsoft Entra SQL administrator and create contained users for the receiver workload identities before running migrations, for example:

```sql
CREATE USER [<managed-identity-name>] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [<managed-identity-name>];
ALTER ROLE db_datawriter ADD MEMBER [<managed-identity-name>];
ALTER ROLE db_ddladmin ADD MEMBER [<managed-identity-name>]; -- migrations only
```

## Receiver persistence

`receivers/backend/src/receiver_reporting.py` mirrors receiver events into SQL when SQL reporting is enabled:

- DEQM `$submit-data` requests populate `dq.SubmissionHistory` and `dq.ProcessingEvents`.
- Computed or received `MeasureReport` payloads populate `dq.MeasureReports` and `dq.QualityMetrics`.
- Workbench measure executions populate processing and quality count metrics.

If SQL is unavailable, the receiver still completes Cosmos DB persistence and logs a warning rather than failing the ingest path.

## Power BI dashboard

Open `receivers/powerbi/ReceiverAnalytics.pbip` in Power BI Desktop. The project contains an Executive View with KPI placeholders for:

- Total submissions
- Active submitters
- Programs onboarded
- Measures processed
- Submission trends
- Numerator / denominator performance
- Operations and validation detail

The semantic model parameters are defined in `ReceiverAnalytics.SemanticModel/definition/expressions.tmdl`:

- `SqlServerName` → set to `AZURE_SQL_SERVER_FQDN`
- `SqlDatabaseName` → set to `AZURE_SQL_DATABASE_NAME`

Publish the report to a Fabric workspace after updating the parameters and validating refresh credentials. Use managed identity or service principal access to Azure SQL where supported by your Fabric tenant.

## Demo data

`receivers/reporting/sample/receiver_analytics_sample.csv` provides a small demonstration dataset with submissions, numerator/denominator values, validation errors, and latency metrics for offline mockups.
