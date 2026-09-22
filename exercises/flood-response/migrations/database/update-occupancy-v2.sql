CREATE OR ALTER PROCEDURE flood.UpdateOccupancy
    @run_id uniqueidentifier,
    @shelter_id uniqueidentifier,
    @occupancy int,
    @expected_version varchar(128),
    @idempotency_key varchar(512)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @RunId uniqueidentifier=@run_id, @ShelterId uniqueidentifier=@shelter_id;
    DECLARE @ExpectedVersion varchar(128)=@expected_version;
    DECLARE @IdempotencyKey varchar(512)=@idempotency_key;
    IF @@TRANCOUNT <> 0
        THROW 51000, 'Call this procedure without an ambient transaction.', 1;
    IF @IdempotencyKey IS NULL OR DATALENGTH(@IdempotencyKey) NOT BETWEEN 1 AND 128
        OR DATALENGTH(@IdempotencyKey) <> LEN(@IdempotencyKey)
        OR @IdempotencyKey COLLATE Latin1_General_100_BIN2 LIKE '%[^A-Za-z0-9._:-]%'
        OR @ExpectedVersion IS NULL OR DATALENGTH(@ExpectedVersion) <> 37
        OR @RunId IS NULL OR @ShelterId IS NULL OR @occupancy IS NULL
        THROW 51022, 'Invalid bounded operation input.', 1;
    IF COALESCE(IS_ROLEMEMBER('flood_injector'),0) <> 1
       AND COALESCE(IS_ROLEMEMBER('flood_operator'),0) <> 1
       AND COALESCE(IS_ROLEMEMBER('db_owner'),0) <> 1
        THROW 51003, 'Injector identity required.', 1;
    DECLARE @Sid varbinary(85)=(SELECT sid FROM sys.database_principals WHERE principal_id=USER_ID());
    DECLARE @Actor varchar(96) = 'sql:' + LOWER(CONVERT(varchar(64),HASHBYTES('SHA2_256',@Sid),2));
    DECLARE @Operation varchar(64) = 'shelter.occupancy.update';
    DECLARE @Fingerprint varchar(64) = LOWER(CONVERT(varchar(64), HASHBYTES('SHA2_256',
        CONCAT(CONVERT(varchar(36),@RunId),'|',CONVERT(varchar(36),@ShelterId),'|',
               CONVERT(varchar(12),@occupancy),'|',@ExpectedVersion)),2));
    DECLARE @Resource nvarchar(255) = 'flood:run:' + LOWER(CONVERT(varchar(36),@RunId));
    DECLARE @LockResult int, @Result nvarchar(max), @PreviousHash varchar(64);
    BEGIN TRY
        BEGIN TRANSACTION;
        EXEC @LockResult=sys.sp_getapplock @Resource=@Resource,
            @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=5000;
        IF @LockResult < 0
            THROW 51009, 'Run busy; reconcile with the same key.', 1;
        IF NOT EXISTS (
            SELECT 1 FROM flood.sql_run_grants WITH (HOLDLOCK)
            WHERE run_id=@RunId AND principal_id=USER_ID() AND capability='inject'
              AND principal_sid=@Sid
              AND expires_at>SYSUTCDATETIME()
        )
            THROW 51003, 'Run access denied.', 1;
        SELECT @PreviousHash=payload_hash, @Result=response_json
        FROM flood.receipts
        WHERE actor_key=@Actor AND run_id=@RunId AND operation=@Operation
          AND idempotency_key=@IdempotencyKey COLLATE Latin1_General_100_BIN2;
        IF @Result IS NOT NULL
        BEGIN
            IF @PreviousHash <> @Fingerprint
                THROW 51009, 'Idempotency payload conflict.', 1;
        END
        ELSE
        BEGIN
            DECLARE @Version uniqueidentifier, @Capacity int, @Before int;
            DECLARE @Now datetime2(6)=SYSUTCDATETIME(), @Event uniqueidentifier=NEWID();
            DECLARE @Correlation uniqueidentifier=NEWID(), @Outcome varchar(16)='succeeded';
            DECLARE @Code varchar(48)=NULL, @Status int=200;
            SELECT @Version=record_version, @Capacity=capacity, @Before=occupancy
            FROM flood.shelters WITH (UPDLOCK,HOLDLOCK)
            WHERE id=@ShelterId AND run_id=@RunId AND retired_at IS NULL;
            IF NOT EXISTS (SELECT 1 FROM flood.runs WHERE id=@RunId AND status='active')
                SET @Code='run_not_active';
            ELSE IF @Version IS NULL
                SET @Code='resource_unavailable';
            ELSE IF @ExpectedVersion COLLATE Latin1_General_100_BIN2 <>
                '"v1:' + LOWER(REPLACE(CONVERT(varchar(36),@Version),'-','')) + '"'
                SET @Code='version_conflict';
            ELSE IF @occupancy < 0 OR @occupancy > @Capacity
                SET @Code='invalid_occupancy';
            IF @Code IS NOT NULL
            BEGIN
                SET @Outcome='rejected';
                SET @Status=409;
            END
            ELSE
            BEGIN
                SET @Version=NEWID();
                UPDATE flood.shelters SET occupancy=@occupancy, record_version=@Version
                WHERE id=@ShelterId AND run_id=@RunId;
            END;
            SET @Result = (
                SELECT 'flood-lab/v1' AS contract_version, @Outcome AS outcome, @Code AS code,
                    @RunId AS run_id, @ShelterId AS shelter_id,
                    CASE WHEN @Outcome='succeeded' THEN @occupancy ELSE @Before END AS occupancy,
                    @Capacity AS capacity,
                    CASE WHEN @Capacity > 0 THEN
                        CONVERT(float, CASE WHEN @Outcome='succeeded' THEN @occupancy ELSE @Before END)
                            * 100.0 / @Capacity
                    END AS occupancy_percent,
                    CASE WHEN @Version IS NOT NULL THEN
                        '"v1:' + LOWER(REPLACE(CONVERT(varchar(36),@Version),'-','')) + '"'
                    END AS record_version,
                    @Event AS durable_event_id,
                    CONVERT(varchar(33),@Now,126)+'Z' AS committed_at,
                    @Correlation AS correlation_id
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES
            );
            DECLARE @Evidence nvarchar(max) = (
                SELECT @Before AS previous_occupancy, @occupancy AS proposed_occupancy,
                    @Code AS code
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES
            );
            INSERT flood.events(id,run_id,operation,actor_key,record_id,record_version,
                outcome,committed_at,correlation_id,data_json)
            VALUES(@Event,@RunId,@Operation,@Actor,@ShelterId,@Version,@Outcome,@Now,
                @Correlation,@Evidence);
            INSERT flood.receipts(id,actor_key,run_id,operation,idempotency_key,payload_hash,
                response_json,http_status,event_id,created_at)
            VALUES(NEWID(),@Actor,@RunId,@Operation,@IdempotencyKey,@Fingerprint,@Result,
                @Status,@Event,@Now);
        END;
        COMMIT TRANSACTION;
        SELECT * FROM OPENJSON(@Result) WITH (
            contract_version varchar(32), outcome varchar(16), code varchar(48),
            run_id uniqueidentifier, shelter_id uniqueidentifier, occupancy int, capacity int,
            occupancy_percent float,
            record_version varchar(37), durable_event_id uniqueidentifier,
            committed_at datetimeoffset(6), correlation_id uniqueidentifier
        );
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
