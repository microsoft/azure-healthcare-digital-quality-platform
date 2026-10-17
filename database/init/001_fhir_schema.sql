CREATE TABLE IF NOT EXISTS fhir_resources (
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    patient_id text,
    resource jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (resource_type, resource_id),
    CONSTRAINT fhir_resource_type_matches
        CHECK (resource_type = resource ->> 'resourceType'),
    CONSTRAINT fhir_resource_id_matches
        CHECK (resource_id = resource ->> 'id')
);

CREATE INDEX IF NOT EXISTS fhir_resources_patient_idx
    ON fhir_resources (patient_id, resource_type);

CREATE INDEX IF NOT EXISTS fhir_resources_resource_gin_idx
    ON fhir_resources USING gin (resource jsonb_path_ops);

CREATE TABLE IF NOT EXISTS terminology_codes (
    value_set_url text NOT NULL,
    value_set_version text NOT NULL DEFAULT '',
    system text NOT NULL,
    code text NOT NULL,
    display text,
    PRIMARY KEY (value_set_url, value_set_version, system, code)
);

CREATE INDEX IF NOT EXISTS terminology_codes_lookup_idx
    ON terminology_codes (value_set_url, system, code);