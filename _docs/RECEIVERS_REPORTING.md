# Receivers Reporting: Azure SQL Database and Power BI

The Receivers stack provisions an Azure SQL Database for operational reporting and includes a source-controlled Power BI Project template at `receivers/powerbi/ReceiverAnalytics.pbip`.

## Provisioned resources

`receivers/_infra/main.bicep` adds:

- Azure SQL logical server (`AZURE_SQL_SERVER_NAME`, `AZURE_SQL_SERVER_FQDN`)
- Azure SQL Database (`AZURE_SQL_DATABASE_NAME`, default `dq_receiver_reporting`)
- Private endpoint and `privatelink.database.windows.net` DNS zone when `vnetEnabled=true`
- Optional Microsoft Entra SQL administrator (`AZURE_SQL_ENTRA_ADMIN_OBJECT_ID`, `AZURE_SQL_ENTRA_ADMIN_LOGIN`)
- Managed-identity ODBC connection string output (`AZURE_SQL_CONNECTION_STRING`), including the receivers managed identity client ID as `UID`

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

Azure SQL requires a break-glass SQL administrator password at server creation. When `AZURE_SQL_AAD_ONLY_AUTH=true`, that password is not accepted for sign-in after the Microsoft Entra administrator and Entra-only setting are applied.

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

Open `receivers/powerbi/ReceiverAnalytics.pbip` in Power BI Desktop. The project contains two report pages:

- **Receiver Analytics** - an executive operations and quality view with total submissions, active submitters, acceptance rate, processing success rate, submission trend, denominator-weighted reported performance by measure, and recent processing-event detail.
- **Report Information** - an in-report guide to metric definitions, Azure SQL data provenance, model parameters, refresh behavior, and interpretation limits.

The semantic model includes a `RowCount` column that is always `1`; the `Total submissions` measure sums it to count submission records consistently after Power Query transformations.

The executive page uses three Azure SQL fact tables:

- `dq.SubmissionHistory` for participation, submission status, program, submitter, measure, cohort, payload type, and received time.
- `dq.MeasureReports` for reported numerator, denominator, exclusions, report context, measurement period, and received time.
- `dq.ProcessingEvents` for processing status, correlation, latency, and error context.

`Weighted reported rate` divides the total reported numerator by the total reported denominator in the current measure context. Compare this rate within one measure because population definitions and whether higher or lower is favorable differ across measures. Do not interpret it as a cross-measure composite score.

The report does not calculate MIPS final score, benchmark points, eligibility, targeted review, or payment adjustment. Those outcomes require additional upstream data and program rules.

The semantic model parameters are defined in `ReceiverAnalytics.SemanticModel/definition/expressions.tmdl`:

- `SqlServerName` → set to `AZURE_SQL_SERVER_FQDN`
- `SqlDatabaseName` → set to `AZURE_SQL_DATABASE_NAME`
- `UseSampleData` → keep `true` for the built-in offline demonstration data or set `false` to query Azure SQL

Source control keeps `UseSampleData=true` so the PBIP renders without Azure credentials. Before publishing against live data, update the SQL parameters, set `UseSampleData=false`, configure managed identity or service principal access where supported, and validate a complete refresh.

Publish the report to a Fabric workspace only after the Azure SQL parameters, credentials, and Service refresh have been validated.

## Demo data

`receivers/reporting/sample/receiver_analytics_sample.csv` provides a small demonstration dataset with submissions, numerator/denominator values, validation errors, and latency metrics for offline mockups.
