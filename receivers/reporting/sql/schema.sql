-- Receiver reporting warehouse schema for Azure SQL Database.
-- Run this after provisioning receivers/_infra/main.bicep. For managed identity
-- access, connect as the SQL Microsoft Entra administrator and create contained
-- users for the workload identities before running these objects.

IF SCHEMA_ID(N'dq') IS NULL
    EXEC(N'CREATE SCHEMA dq');
GO

CREATE TABLE dq.Programs (
    ProgramId           NVARCHAR(100) NOT NULL CONSTRAINT PK_Programs PRIMARY KEY,
    ProgramName         NVARCHAR(200) NOT NULL,
    AgencyName          NVARCHAR(200) NULL,
    ReportingYear       INT NULL,
    CreatedAtUtc        DATETIME2(3) NOT NULL CONSTRAINT DF_Programs_CreatedAtUtc DEFAULT SYSUTCDATETIME()
);
GO

CREATE TABLE dq.Measures (
    MeasureId           NVARCHAR(100) NOT NULL CONSTRAINT PK_Measures PRIMARY KEY,
    MeasureName         NVARCHAR(300) NULL,
    MeasureVersion      NVARCHAR(50) NULL,
    MeasureDomain       NVARCHAR(100) NULL,
    CreatedAtUtc        DATETIME2(3) NOT NULL CONSTRAINT DF_Measures_CreatedAtUtc DEFAULT SYSUTCDATETIME()
);
GO

CREATE TABLE dq.Submitters (
    SubmitterId         NVARCHAR(100) NOT NULL CONSTRAINT PK_Submitters PRIMARY KEY,
    SubmitterName       NVARCHAR(300) NULL,
    OrganizationType    NVARCHAR(100) NULL,
    ProgramId           NVARCHAR(100) NULL,
    CreatedAtUtc        DATETIME2(3) NOT NULL CONSTRAINT DF_Submitters_CreatedAtUtc DEFAULT SYSUTCDATETIME(),
    CONSTRAINT FK_Submitters_Programs FOREIGN KEY (ProgramId) REFERENCES dq.Programs(ProgramId)
);
GO

CREATE TABLE dq.Cohorts (
    CohortId            NVARCHAR(100) NOT NULL CONSTRAINT PK_Cohorts PRIMARY KEY,
    CohortName          NVARCHAR(300) NULL,
    ProgramId           NVARCHAR(100) NULL,
    MemberCount         INT NULL,
    PeriodStart         DATE NULL,
    PeriodEnd           DATE NULL,
    CreatedAtUtc        DATETIME2(3) NOT NULL CONSTRAINT DF_Cohorts_CreatedAtUtc DEFAULT SYSUTCDATETIME(),
    CONSTRAINT FK_Cohorts_Programs FOREIGN KEY (ProgramId) REFERENCES dq.Programs(ProgramId)
);
GO

CREATE TABLE dq.SubmissionHistory (
    SubmissionId        NVARCHAR(100) NOT NULL CONSTRAINT PK_SubmissionHistory PRIMARY KEY,
    MeasureId           NVARCHAR(100) NOT NULL,
    SubmitterId         NVARCHAR(100) NOT NULL,
    ProgramId           NVARCHAR(100) NULL,
    CohortId            NVARCHAR(100) NULL,
    ReceivedAtUtc       DATETIME2(3) NOT NULL,
    Status              NVARCHAR(50) NOT NULL,
    PayloadType         NVARCHAR(100) NULL,
    PayloadJson         NVARCHAR(MAX) NULL,
    CONSTRAINT FK_SubmissionHistory_Measures FOREIGN KEY (MeasureId) REFERENCES dq.Measures(MeasureId)
);
GO

CREATE TABLE dq.MeasureReports (
    MeasureReportId     NVARCHAR(100) NOT NULL CONSTRAINT PK_MeasureReports PRIMARY KEY,
    SubmissionId        NVARCHAR(100) NULL,
    MeasureId           NVARCHAR(100) NOT NULL,
    SubmitterId         NVARCHAR(100) NOT NULL,
    ProgramId           NVARCHAR(100) NULL,
    SubjectId           NVARCHAR(100) NULL,
    ReportType          NVARCHAR(50) NULL,
    PeriodStart         DATE NULL,
    PeriodEnd           DATE NULL,
    Numerator           INT NULL,
    Denominator         INT NULL,
    Exclusions          INT NULL,
    PerformanceRate     DECIMAL(9,4) NULL,
    Status              NVARCHAR(50) NOT NULL,
    ReceivedAtUtc       DATETIME2(3) NOT NULL,
    PayloadJson         NVARCHAR(MAX) NULL,
    CONSTRAINT FK_MeasureReports_Measures FOREIGN KEY (MeasureId) REFERENCES dq.Measures(MeasureId),
    CONSTRAINT FK_MeasureReports_Submissions FOREIGN KEY (SubmissionId) REFERENCES dq.SubmissionHistory(SubmissionId)
);
GO

CREATE TABLE dq.ProcessingEvents (
    ProcessingEventId   BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_ProcessingEvents PRIMARY KEY,
    CorrelationId       NVARCHAR(100) NULL,
    EventType           NVARCHAR(100) NOT NULL,
    MeasureId           NVARCHAR(400) NULL,
    SubmitterId         NVARCHAR(100) NULL,
    ProgramId           NVARCHAR(100) NULL,
    Status              NVARCHAR(50) NOT NULL,
    LatencyMs           INT NULL,
    ErrorCode           NVARCHAR(100) NULL,
    ErrorMessage        NVARCHAR(1000) NULL,
    CreatedAtUtc        DATETIME2(3) NOT NULL CONSTRAINT DF_ProcessingEvents_CreatedAtUtc DEFAULT SYSUTCDATETIME()
);
GO

CREATE TABLE dq.AuditLogs (
    AuditLogId          BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_AuditLogs PRIMARY KEY,
    ActorId             NVARCHAR(200) NULL,
    Action              NVARCHAR(100) NOT NULL,
    ResourceType        NVARCHAR(100) NOT NULL,
    ResourceId          NVARCHAR(100) NULL,
    CreatedAtUtc        DATETIME2(3) NOT NULL CONSTRAINT DF_AuditLogs_CreatedAtUtc DEFAULT SYSUTCDATETIME(),
    DetailsJson         NVARCHAR(MAX) NULL
);
GO

CREATE TABLE dq.QualityMetrics (
    QualityMetricId     BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_QualityMetrics PRIMARY KEY,
    MeasureId           NVARCHAR(100) NOT NULL,
    ProgramId           NVARCHAR(100) NULL,
    SubmitterId         NVARCHAR(100) NULL,
    PeriodStart         DATE NULL,
    PeriodEnd           DATE NULL,
    MetricName          NVARCHAR(100) NOT NULL,
    MetricValue         DECIMAL(18,4) NOT NULL,
    CreatedAtUtc        DATETIME2(3) NOT NULL CONSTRAINT DF_QualityMetrics_CreatedAtUtc DEFAULT SYSUTCDATETIME(),
    CONSTRAINT FK_QualityMetrics_Measures FOREIGN KEY (MeasureId) REFERENCES dq.Measures(MeasureId)
);
GO

CREATE INDEX IX_SubmissionHistory_ReceivedAtUtc ON dq.SubmissionHistory(ReceivedAtUtc);
CREATE INDEX IX_MeasureReports_Measure_Period ON dq.MeasureReports(MeasureId, PeriodStart, PeriodEnd);
CREATE INDEX IX_ProcessingEvents_CreatedAtUtc ON dq.ProcessingEvents(CreatedAtUtc);
CREATE INDEX IX_QualityMetrics_Measure_Period ON dq.QualityMetrics(MeasureId, PeriodStart, PeriodEnd);
GO
