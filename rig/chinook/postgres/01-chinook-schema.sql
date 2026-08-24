-- Chinook schema for the crawler acceptance rig — PostgreSQL.
--
-- Schema only, and deliberately so: session 2 crawls Tier A, which reads the
-- dictionary and never touches a row. Session 3 measures data and will need
-- the full Chinook load (see rig/README.md).
--
-- Naming follows the snake_case Chinook variant the OKF fixtures were
-- generated from (album.album_id, not Album.AlbumId), so the rig and the
-- fixtures describe the same estate in the same words. Types are the
-- canonical PostgreSQL Chinook types: varchar / integer / numeric(10,2) /
-- timestamp.

CREATE TABLE artist (
    artist_id  integer      NOT NULL,
    name       varchar(120),
    CONSTRAINT pk_artist PRIMARY KEY (artist_id)
);

CREATE TABLE album (
    album_id   integer      NOT NULL,
    title      varchar(160) NOT NULL,
    artist_id  integer      NOT NULL,
    CONSTRAINT pk_album PRIMARY KEY (album_id),
    CONSTRAINT fk_album_artist FOREIGN KEY (artist_id) REFERENCES artist (artist_id)
);

CREATE TABLE genre (
    genre_id   integer      NOT NULL,
    name       varchar(120),
    CONSTRAINT pk_genre PRIMARY KEY (genre_id)
);

CREATE TABLE media_type (
    media_type_id integer   NOT NULL,
    name          varchar(120),
    CONSTRAINT pk_media_type PRIMARY KEY (media_type_id)
);

CREATE TABLE track (
    track_id      integer      NOT NULL,
    name          varchar(200) NOT NULL,
    album_id      integer,
    media_type_id integer      NOT NULL,
    genre_id      integer,
    composer      varchar(220),
    milliseconds  integer      NOT NULL,
    bytes         integer,
    unit_price    numeric(10,2) NOT NULL,
    CONSTRAINT pk_track PRIMARY KEY (track_id),
    CONSTRAINT fk_track_album FOREIGN KEY (album_id) REFERENCES album (album_id),
    CONSTRAINT fk_track_media_type FOREIGN KEY (media_type_id)
        REFERENCES media_type (media_type_id),
    CONSTRAINT fk_track_genre FOREIGN KEY (genre_id) REFERENCES genre (genre_id)
);

CREATE TABLE playlist (
    playlist_id integer      NOT NULL,
    name        varchar(120),
    CONSTRAINT pk_playlist PRIMARY KEY (playlist_id)
);

CREATE TABLE playlist_track (
    playlist_id integer NOT NULL,
    track_id    integer NOT NULL,
    CONSTRAINT pk_playlist_track PRIMARY KEY (playlist_id, track_id),
    CONSTRAINT fk_playlist_track_playlist FOREIGN KEY (playlist_id)
        REFERENCES playlist (playlist_id),
    CONSTRAINT fk_playlist_track_track FOREIGN KEY (track_id)
        REFERENCES track (track_id)
);

CREATE TABLE employee (
    employee_id integer     NOT NULL,
    last_name   varchar(20) NOT NULL,
    first_name  varchar(20) NOT NULL,
    title       varchar(30),
    reports_to  integer,
    birth_date  timestamp,
    hire_date   timestamp,
    address     varchar(70),
    city        varchar(40),
    state       varchar(40),
    country     varchar(40),
    postal_code varchar(10),
    phone       varchar(24),
    fax         varchar(24),
    email       varchar(60),
    CONSTRAINT pk_employee PRIMARY KEY (employee_id),
    CONSTRAINT fk_employee_reports_to FOREIGN KEY (reports_to)
        REFERENCES employee (employee_id)
);

CREATE TABLE customer (
    customer_id    integer     NOT NULL,
    first_name     varchar(40) NOT NULL,
    last_name      varchar(20) NOT NULL,
    company        varchar(80),
    address        varchar(70),
    city           varchar(40),
    state          varchar(40),
    country        varchar(40),
    postal_code    varchar(10),
    phone          varchar(24),
    fax            varchar(24),
    email          varchar(60) NOT NULL,
    support_rep_id integer,
    CONSTRAINT pk_customer PRIMARY KEY (customer_id),
    CONSTRAINT fk_customer_support_rep FOREIGN KEY (support_rep_id)
        REFERENCES employee (employee_id)
);

CREATE TABLE invoice (
    invoice_id           integer      NOT NULL,
    customer_id          integer      NOT NULL,
    invoice_date         timestamp    NOT NULL,
    billing_address      varchar(70),
    billing_city         varchar(40),
    billing_state        varchar(40),
    billing_country      varchar(40),
    billing_postal_code  varchar(10),
    total                numeric(10,2) NOT NULL,
    CONSTRAINT pk_invoice PRIMARY KEY (invoice_id),
    CONSTRAINT fk_invoice_customer FOREIGN KEY (customer_id)
        REFERENCES customer (customer_id)
);

CREATE TABLE invoice_line (
    invoice_line_id integer      NOT NULL,
    invoice_id      integer      NOT NULL,
    track_id        integer      NOT NULL,
    unit_price      numeric(10,2) NOT NULL,
    quantity        integer      NOT NULL,
    CONSTRAINT pk_invoice_line PRIMARY KEY (invoice_line_id),
    CONSTRAINT fk_invoice_line_invoice FOREIGN KEY (invoice_id)
        REFERENCES invoice (invoice_id),
    CONSTRAINT fk_invoice_line_track FOREIGN KEY (track_id)
        REFERENCES track (track_id)
);

-- Chinook's secondary indexes: one per foreign key column. On a legacy
-- database these are exactly the join-intent signal A4 is there to read.
CREATE INDEX ifk_album_artist_id          ON album (artist_id);
CREATE INDEX ifk_customer_support_rep_id  ON customer (support_rep_id);
CREATE INDEX ifk_employee_reports_to      ON employee (reports_to);
CREATE INDEX ifk_invoice_customer_id      ON invoice (customer_id);
CREATE INDEX ifk_invoice_line_invoice_id  ON invoice_line (invoice_id);
CREATE INDEX ifk_invoice_line_track_id    ON invoice_line (track_id);
CREATE INDEX ifk_playlist_track_track_id  ON playlist_track (track_id);
CREATE INDEX ifk_track_album_id           ON track (album_id);
CREATE INDEX ifk_track_genre_id           ON track (genre_id);
CREATE INDEX ifk_track_media_type_id      ON track (media_type_id);

-- Analyse at load so A6 has dictionary statistics to read. Without it
-- pg_class.reltuples stays -1 (never analysed), which the crawler correctly
-- reads as "no statistics" — a real signal, but not the one this rig is for.
ANALYZE;
