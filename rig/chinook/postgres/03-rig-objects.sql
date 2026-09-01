-- Session-6b rig objects: A7 acceptance needs exactly two code objects.
--
-- 1. A view joining two Chinook tables. The crawl must mine its ON
--    predicate into join-intent lines on album.artist_id and
--    artist.artist_id (PostgreSQL stores the definition re-rendered by
--    pg_get_viewdef — parenthesised join tree, double-parened predicate —
--    which is exactly the shape the extractor's tests cover).
--
-- 2. A deliberately unparseable object: a plpgsql function that assembles
--    its SQL at run time. The extractor must refuse it (dynamic-sql) and
--    the crawl must count it as unparsed — never guess at it.

CREATE VIEW album_artist_names AS
SELECT al.title, ar.name
FROM album al
JOIN artist ar ON al.artist_id = ar.artist_id;

CREATE FUNCTION rig_dynamic_count(tbl text) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE n bigint;
BEGIN
    EXECUTE 'SELECT COUNT(*) FROM ' || tbl INTO n;
    RETURN n;
END $$;
