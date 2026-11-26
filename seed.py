"""Wypełnia bazę przykładowymi danymi (używane do zrzutów ekranu w sprawozdaniu)."""
import os
import sys
from contextlib import closing
from datetime import timedelta

import services as svc


def isbn13(prefix12: str) -> str:
    s = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(prefix12))
    return prefix12 + str((10 - s % 10) % 10)


BOOKS = [
    ("Diuna", "Frank Herbert", "9788375780635", 2008),
    ("Solaris", "Stanisław Lem", isbn13("978831000001"), 2013),
    ("Lalka", "Bolesław Prus", isbn13("978831000002"), 2010),
    ("Pan Tadeusz", "Adam Mickiewicz", isbn13("978831000003"), 2005),
    ("Wiedźmin: Ostatnie życzenie", "Andrzej Sapkowski", isbn13("978831000004"), 2014),
    ("Quo vadis", "Henryk Sienkiewicz", isbn13("978831000005"), 2012),
    ("Zbrodnia i kara", "Fiodor Dostojewski", isbn13("978831000006"), 2011),
    ("Rok 1984", "George Orwell", isbn13("978831000007"), 2015),
    ("Mistrz i Małgorzata", "Michaił Bułhakow", isbn13("978831000008"), 2009),
    ("Ferdydurke", "Witold Gombrowicz", isbn13("978831000009"), 2007),
    ("Cyberiada", "Stanisław Lem", isbn13("978831000010"), 2012),
    ("Hobbit", "J. R. R. Tolkien", isbn13("978831000011"), 2016),
    ("Dżuma", "Albert Camus", isbn13("978831000012"), 2006),
    ("Chłopi", "Władysław Reymont", isbn13("978831000013"), 2004),
    ("Granica", "Zofia Nałkowska", isbn13("978831000014"), 2013),
    ("Ziemia obiecana", "Władysław Reymont", isbn13("978831000015"), 2003),
    ("Wojna światów", "H. G. Wells", isbn13("978831000016"), 2017),
    ("Dziady", "Adam Mickiewicz", isbn13("978831000017"), 2002),
]
READERS = [
    ("Anna", "Kowalska", "anna.kowalska@example.pl", "+48 600 100 200"),
    ("Piotr", "Nowak", "piotr.nowak@example.pl", "+48 601 200 300"),
    ("Marta", "Wiśniewska", "marta.w@example.pl", None),
    ("Łukasz", "Żak", "lukasz.zak@example.pl", "+48 602 300 400"),
]


def seed(conn):
    ids = [svc.add_book(conn, *b[:3], b[3]) for b in BOOKS]
    rid = [svc.add_reader(conn, *r) for r in READERS]
    t = svc.today()
    def raw(book, reader, start, due, back=None):
        with conn:
            conn.execute(
                "INSERT INTO Wypozyczenia (id_ksiazki,id_czytelnika,data_wypozyczenia,termin_zwrotu,data_zwrotu)"
                " VALUES (?,?,?,?,?)",
                (ids[book], rid[reader], start.isoformat(), due.isoformat(), back.isoformat() if back else None),
            )
            if back is None:
                conn.execute("UPDATE Ksiazki SET status='Wypożyczona' WHERE id_ksiazki=?", (ids[book],))
    raw(0, 0, t - timedelta(days=90), t - timedelta(days=60), t - timedelta(days=65))
    raw(1, 1, t - timedelta(days=70), t - timedelta(days=40), t - timedelta(days=45))
    raw(0, 1, t - timedelta(days=50), t - timedelta(days=20), t - timedelta(days=22))
    raw(7, 2, t - timedelta(days=35), t - timedelta(days=5))            # po terminie
    raw(4, 0, t - timedelta(days=12), t + timedelta(days=18))
    raw(11, 3, t - timedelta(days=4), t + timedelta(days=26))
    raw(2, 2, t - timedelta(days=2), t + timedelta(days=28))


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BOOKNEST_DB", "booknest.db")
    with closing(svc.connect(path)) as conn:
        svc.init_db(conn)
        if svc.list_books(conn) or svc.list_readers(conn):
            print("Baza zawiera juz dane.")
        else:
            seed(conn)
            print("Dane demonstracyjne zapisane.")
