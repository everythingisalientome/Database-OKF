-- Chinook schema for the crawler acceptance rig — SQL Server.
--
-- Same 11 tables, same keys, same indexes as the PostgreSQL script, in this
-- engine's own types: nvarchar / int / numeric(10,2) / datetime, as the
-- canonical Chinook SQL Server distribution declares them. The crawler is
-- expected to canonicalise nvarchar(160) to NVARCHAR(160) and datetime to
-- TIMESTAMP — the point of the cross-engine acceptance is that the same
-- logical column reads the same way from two different dictionaries, not
-- that both engines pretend to be PostgreSQL.
--
-- Schema only; see rig/README.md.

IF DB_ID('chinook') IS NULL
    CREATE DATABASE chinook;
GO

USE chinook;
GO

CREATE TABLE artist (
    artist_id  int           NOT NULL,
    name       nvarchar(120) NULL,
    CONSTRAINT pk_artist PRIMARY KEY (artist_id)
);

CREATE TABLE album (
    album_id   int           NOT NULL,
    title      nvarchar(160) NOT NULL,
    artist_id  int           NOT NULL,
    CONSTRAINT pk_album PRIMARY KEY (album_id),
    CONSTRAINT fk_album_artist FOREIGN KEY (artist_id) REFERENCES artist (artist_id)
);

CREATE TABLE genre (
    genre_id   int           NOT NULL,
    name       nvarchar(120) NULL,
    CONSTRAINT pk_genre PRIMARY KEY (genre_id)
);

CREATE TABLE media_type (
    media_type_id int        NOT NULL,
    name          nvarchar(120) NULL,
    CONSTRAINT pk_media_type PRIMARY KEY (media_type_id)
);

CREATE TABLE track (
    track_id      int           NOT NULL,
    name          nvarchar(200) NOT NULL,
    album_id      int           NULL,
    media_type_id int           NOT NULL,
    genre_id      int           NULL,
    composer      nvarchar(220) NULL,
    milliseconds  int           NOT NULL,
    bytes         int           NULL,
    unit_price    numeric(10,2) NOT NULL,
    CONSTRAINT pk_track PRIMARY KEY (track_id),
    CONSTRAINT fk_track_album FOREIGN KEY (album_id) REFERENCES album (album_id),
    CONSTRAINT fk_track_media_type FOREIGN KEY (media_type_id)
        REFERENCES media_type (media_type_id),
    CONSTRAINT fk_track_genre FOREIGN KEY (genre_id) REFERENCES genre (genre_id)
);

CREATE TABLE playlist (
    playlist_id int           NOT NULL,
    name        nvarchar(120) NULL,
    CONSTRAINT pk_playlist PRIMARY KEY (playlist_id)
);

CREATE TABLE playlist_track (
    playlist_id int NOT NULL,
    track_id    int NOT NULL,
    CONSTRAINT pk_playlist_track PRIMARY KEY (playlist_id, track_id),
    CONSTRAINT fk_playlist_track_playlist FOREIGN KEY (playlist_id)
        REFERENCES playlist (playlist_id),
    CONSTRAINT fk_playlist_track_track FOREIGN KEY (track_id)
        REFERENCES track (track_id)
);

CREATE TABLE employee (
    employee_id int          NOT NULL,
    last_name   nvarchar(20) NOT NULL,
    first_name  nvarchar(20) NOT NULL,
    title       nvarchar(30) NULL,
    reports_to  int          NULL,
    birth_date  datetime     NULL,
    hire_date   datetime     NULL,
    address     nvarchar(70) NULL,
    city        nvarchar(40) NULL,
    state       nvarchar(40) NULL,
    country     nvarchar(40) NULL,
    postal_code nvarchar(10) NULL,
    phone       nvarchar(24) NULL,
    fax         nvarchar(24) NULL,
    email       nvarchar(60) NULL,
    CONSTRAINT pk_employee PRIMARY KEY (employee_id),
    CONSTRAINT fk_employee_reports_to FOREIGN KEY (reports_to)
        REFERENCES employee (employee_id)
);

CREATE TABLE customer (
    customer_id    int          NOT NULL,
    first_name     nvarchar(40) NOT NULL,
    last_name      nvarchar(20) NOT NULL,
    company        nvarchar(80) NULL,
    address        nvarchar(70) NULL,
    city           nvarchar(40) NULL,
    state          nvarchar(40) NULL,
    country        nvarchar(40) NULL,
    postal_code    nvarchar(10) NULL,
    phone          nvarchar(24) NULL,
    fax            nvarchar(24) NULL,
    email          nvarchar(60) NOT NULL,
    support_rep_id int          NULL,
    CONSTRAINT pk_customer PRIMARY KEY (customer_id),
    CONSTRAINT fk_customer_support_rep FOREIGN KEY (support_rep_id)
        REFERENCES employee (employee_id)
);

CREATE TABLE invoice (
    invoice_id          int           NOT NULL,
    customer_id         int           NOT NULL,
    invoice_date        datetime      NOT NULL,
    billing_address     nvarchar(70)  NULL,
    billing_city        nvarchar(40)  NULL,
    billing_state       nvarchar(40)  NULL,
    billing_country     nvarchar(40)  NULL,
    billing_postal_code nvarchar(10)  NULL,
    total               numeric(10,2) NOT NULL,
    CONSTRAINT pk_invoice PRIMARY KEY (invoice_id),
    CONSTRAINT fk_invoice_customer FOREIGN KEY (customer_id)
        REFERENCES customer (customer_id)
);

CREATE TABLE invoice_line (
    invoice_line_id int           NOT NULL,
    invoice_id      int           NOT NULL,
    track_id        int           NOT NULL,
    unit_price      numeric(10,2) NOT NULL,
    quantity        int           NOT NULL,
    CONSTRAINT pk_invoice_line PRIMARY KEY (invoice_line_id),
    CONSTRAINT fk_invoice_line_invoice FOREIGN KEY (invoice_id)
        REFERENCES invoice (invoice_id),
    CONSTRAINT fk_invoice_line_track FOREIGN KEY (track_id)
        REFERENCES track (track_id)
);
GO

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
GO
