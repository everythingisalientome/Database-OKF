-- Session-6b rig objects: A7 acceptance needs exactly two code objects.
-- Same pair as the PostgreSQL script, in this engine's own idiom: a view
-- whose ON predicate must become join-intent lines, and a dynamic-SQL
-- procedure the extractor must count as unparsed rather than guess at.

USE chinook;
GO

CREATE VIEW dbo.album_artist_names AS
SELECT al.title, ar.name
FROM dbo.album al
JOIN dbo.artist ar ON al.artist_id = ar.artist_id;
GO

CREATE PROCEDURE dbo.rig_dynamic_count @tbl sysname AS
BEGIN
    DECLARE @sql nvarchar(400) = N'SELECT COUNT(*) FROM ' + QUOTENAME(@tbl);
    EXEC (@sql);
END;
GO
