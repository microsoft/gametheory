CREATE OR ALTER PROCEDURE flood.ReadOccupancy
    @run_id uniqueidentifier,
    @shelter_id uniqueidentifier
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @RunId uniqueidentifier=@run_id, @ShelterId uniqueidentifier=@shelter_id;
    IF NOT EXISTS (
        SELECT 1 FROM flood.sql_run_grants
        WHERE run_id=@RunId AND principal_id=USER_ID()
          AND principal_sid=(SELECT sid FROM sys.database_principals WHERE principal_id=USER_ID())
          AND capability IN ('observe','inject') AND expires_at > SYSUTCDATETIME()
    )
        THROW 51003, 'Run access denied.', 1;
    IF NOT EXISTS (
        SELECT 1 FROM flood.shelters
        WHERE id=@ShelterId AND run_id=@RunId AND retired_at IS NULL
    )
        THROW 51004, 'Shelter unavailable in this run.', 1;
    SELECT s.run_id, s.id AS shelter_id, s.name, s.capacity, s.occupancy,
        CONVERT(float, s.occupancy) * 100.0 / s.capacity AS occupancy_percent,
        '"v1:' + LOWER(REPLACE(CONVERT(varchar(36),s.record_version),'-','')) + '"'
            AS record_version,
        e.id AS durable_event_id,
        CONVERT(datetimeoffset(6), e.committed_at) AS committed_at
    FROM flood.shelters s
    OUTER APPLY (
        SELECT TOP (1) id, committed_at
        FROM flood.events
        WHERE run_id=@RunId AND record_id=@ShelterId AND outcome='succeeded'
          AND record_version=s.record_version
        ORDER BY sequence DESC
    ) e
    WHERE s.run_id=@RunId AND s.id=@ShelterId AND s.retired_at IS NULL;
END;
