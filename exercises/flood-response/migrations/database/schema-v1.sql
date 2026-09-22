CREATE TABLE flood.database_identity (
    id int NOT NULL PRIMARY KEY CHECK (id=1),
    contract_version varchar(32) NOT NULL,
    database_name varchar(128) NOT NULL,
    purpose varchar(16) NOT NULL CHECK (purpose IN ('exercise','disposable-tests'))
);
CREATE TABLE flood.runs (
    id uniqueidentifier NOT NULL PRIMARY KEY,
    name nvarchar(120) NOT NULL,
    status varchar(16) NOT NULL CHECK (status IN ('active','recovered')),
    owner_principal nvarchar(128) NOT NULL,
    owner_principal_sid varbinary(85) NOT NULL,
    owner_operation uniqueidentifier NOT NULL,
    profile_version varchar(32) NOT NULL,
    manifest_hash varchar(64) NOT NULL,
    seed_manifest nvarchar(max) NOT NULL CHECK (ISJSON(seed_manifest)=1),
    record_version uniqueidentifier NOT NULL,
    created_at datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE TABLE flood.run_grants (
    run_id uniqueidentifier NOT NULL REFERENCES flood.runs(id),
    tenant_id uniqueidentifier NOT NULL,
    object_id uniqueidentifier NOT NULL,
    principal_kind varchar(16) NOT NULL CHECK (principal_kind IN ('user','service')),
    role varchar(16) NOT NULL CHECK (role IN ('participant','api','observer')),
    active bit NOT NULL,
    expires_at datetime2(6) NOT NULL,
    PRIMARY KEY (run_id,tenant_id,object_id,principal_kind),
    CHECK ((role='participant' AND principal_kind='user') OR
           (role='api' AND principal_kind='service') OR role='observer')
);
CREATE TABLE flood.sql_run_grants (
    run_id uniqueidentifier NOT NULL REFERENCES flood.runs(id),
    principal_id int NOT NULL,
    principal_sid varbinary(85) NOT NULL,
    capability varchar(16) NOT NULL CHECK (capability IN ('observe','inject')),
    expires_at datetime2(6) NOT NULL,
    PRIMARY KEY (run_id,principal_id)
);
CREATE TABLE flood.shelters (
    id uniqueidentifier NOT NULL PRIMARY KEY,
    run_id uniqueidentifier NOT NULL REFERENCES flood.runs(id),
    name nvarchar(120) NOT NULL,
    capacity int NOT NULL CHECK (capacity BETWEEN 1 AND 100000),
    occupancy int NOT NULL,
    owner_operation uniqueidentifier NOT NULL,
    seed_version uniqueidentifier NOT NULL,
    record_version uniqueidentifier NOT NULL,
    retired_at datetime2(6) NULL,
    CONSTRAINT uq_shelter_run_id UNIQUE(run_id,id),
    CHECK (occupancy BETWEEN 0 AND capacity)
);
CREATE INDEX ix_shelters_run_id ON flood.shelters(run_id);
CREATE TABLE flood.requests (
    id uniqueidentifier NOT NULL PRIMARY KEY,
    sequence bigint IDENTITY NOT NULL UNIQUE,
    run_id uniqueidentifier NOT NULL REFERENCES flood.runs(id),
    shelter_id uniqueidentifier NOT NULL,
    resource_type varchar(32) NOT NULL
        CHECK (resource_type IN ('cots','blankets','water_cases','transport_seats')),
    quantity_requested int NOT NULL CHECK (quantity_requested BETWEEN 1 AND 10000),
    quantity_allocated int NOT NULL,
    status varchar(24) NOT NULL CHECK (status IN ('open','acknowledged','fulfilled')),
    summary nvarchar(240) NOT NULL,
    needed_by datetime2(6) NOT NULL,
    created_at datetime2(6) NOT NULL,
    acknowledged_at datetime2(6) NULL,
    acknowledged_by varchar(96) NULL,
    owner_operation uniqueidentifier NOT NULL,
    seed_version uniqueidentifier NULL,
    record_version uniqueidentifier NOT NULL,
    retired_at datetime2(6) NULL,
    CONSTRAINT uq_request_run_id UNIQUE(run_id,id),
    CONSTRAINT fk_request_shelter_run FOREIGN KEY (run_id,shelter_id)
        REFERENCES flood.shelters(run_id,id),
    CHECK (quantity_allocated BETWEEN 0 AND quantity_requested)
);
CREATE INDEX ix_requests_run_id ON flood.requests(run_id);
CREATE TABLE flood.allocations (
    id uniqueidentifier NOT NULL PRIMARY KEY,
    run_id uniqueidentifier NOT NULL REFERENCES flood.runs(id),
    request_id uniqueidentifier NOT NULL,
    quantity int NOT NULL CHECK (quantity BETWEEN 1 AND 10000),
    available_at datetime2(6) NOT NULL,
    created_at datetime2(6) NOT NULL,
    actor_key varchar(96) NOT NULL,
    owner_operation uniqueidentifier NOT NULL,
    seed_version uniqueidentifier NULL,
    record_version uniqueidentifier NOT NULL,
    retired_at datetime2(6) NULL,
    CONSTRAINT fk_allocation_request_run FOREIGN KEY (run_id,request_id)
        REFERENCES flood.requests(run_id,id)
);
CREATE INDEX ix_allocations_run_id ON flood.allocations(run_id);
CREATE TABLE flood.events (
    id uniqueidentifier NOT NULL PRIMARY KEY,
    sequence bigint IDENTITY NOT NULL UNIQUE,
    run_id uniqueidentifier NOT NULL REFERENCES flood.runs(id),
    operation varchar(64) NOT NULL,
    actor_key varchar(96) NOT NULL,
    record_id uniqueidentifier NULL,
    record_version uniqueidentifier NULL,
    outcome varchar(16) NOT NULL CHECK (outcome IN ('succeeded','rejected','failed','unknown')),
    committed_at datetime2(6) NOT NULL,
    correlation_id uniqueidentifier NOT NULL,
    data_json nvarchar(max) NOT NULL CHECK (ISJSON(data_json)=1)
);
CREATE INDEX ix_events_run_id ON flood.events(run_id, sequence);
CREATE TABLE flood.receipts (
    id uniqueidentifier NOT NULL PRIMARY KEY,
    actor_key varchar(96) NOT NULL,
    run_id uniqueidentifier NOT NULL REFERENCES flood.runs(id),
    operation varchar(64) NOT NULL,
    idempotency_key varchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
    payload_hash varchar(64) NOT NULL,
    response_json nvarchar(max) NOT NULL CHECK (ISJSON(response_json)=1),
    http_status int NOT NULL,
    event_id uniqueidentifier NOT NULL REFERENCES flood.events(id),
    created_at datetime2(6) NOT NULL,
    CONSTRAINT uq_receipt_identity UNIQUE(actor_key,run_id,operation,idempotency_key)
);
CREATE TABLE flood.recovery_evidence (
    id uniqueidentifier NOT NULL PRIMARY KEY,
    run_id uniqueidentifier NOT NULL REFERENCES flood.runs(id),
    owner_operation uniqueidentifier NOT NULL,
    operator_principal nvarchar(128) NOT NULL,
    created_at datetime2(6) NOT NULL,
    result_json nvarchar(max) NOT NULL CHECK (ISJSON(result_json)=1)
);
CREATE INDEX ix_recovery_evidence_run_id ON flood.recovery_evidence(run_id);
