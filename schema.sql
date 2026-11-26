-- schemat relacyjnej bazy danych sqllite
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS Czytelnicy (
    id_czytelnika INTEGER PRIMARY KEY AUTOINCREMENT,
    imie          TEXT NOT NULL CHECK (length(trim(imie)) > 0),
    nazwisko      TEXT NOT NULL CHECK (length(trim(nazwisko)) > 0),
    email         TEXT UNIQUE,
    telefon       TEXT
);

CREATE TABLE IF NOT EXISTS Ksiazki (
    id_ksiazki  INTEGER PRIMARY KEY AUTOINCREMENT,
    tytul       TEXT NOT NULL CHECK (length(trim(tytul)) > 0),
    autor       TEXT NOT NULL CHECK (length(trim(autor)) > 0),
    isbn        TEXT UNIQUE,
    rok_wydania INTEGER CHECK (rok_wydania IS NULL OR rok_wydania BETWEEN 1400 AND 2100),
    status      TEXT NOT NULL DEFAULT 'Dostępna'
                CHECK (status IN ('Dostępna', 'Wypożyczona'))
);

CREATE TABLE IF NOT EXISTS Wypozyczenia (
    id_wypozyczenia   INTEGER PRIMARY KEY AUTOINCREMENT,
    id_ksiazki        INTEGER NOT NULL
                      REFERENCES Ksiazki(id_ksiazki) ON DELETE RESTRICT,
    id_czytelnika     INTEGER NOT NULL
                      REFERENCES Czytelnicy(id_czytelnika) ON DELETE RESTRICT,
    data_wypozyczenia DATE NOT NULL,
    termin_zwrotu     DATE NOT NULL,
    data_zwrotu       DATE,
    CHECK (termin_zwrotu >= data_wypozyczenia),
    CHECK (data_zwrotu IS NULL OR data_zwrotu >= data_wypozyczenia)
);

-- jedna książka max. 1 aktywne wypożyczenie
CREATE UNIQUE INDEX IF NOT EXISTS ux_aktywne_wypozyczenie
    ON Wypozyczenia(id_ksiazki) WHERE data_zwrotu IS NULL;

CREATE INDEX IF NOT EXISTS ix_wyp_czytelnik ON Wypozyczenia(id_czytelnika);
