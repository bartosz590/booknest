from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
LOAN_DAYS = 30
EXPORT_VERSION = 1

STATUS_AVAILABLE = "Dostępna"
STATUS_LOANED = "Wypożyczona"


class BookNestError(Exception):
    """Błąd walidacji lub reguły biznesowej - komunikat jest wyświetlany użytkownikowi."""


def today() -> date:
    return date.today()


# połączenie
_PL_ALPHABET = "aąbcćdeęfghijklłmnńoóprsśtuwyzźż"
_PL_ORDER = {ch: i for i, ch in enumerate(_PL_ALPHABET)}


def _pl_key(text: str):
    return [_PL_ORDER.get(ch, 100 + ord(ch)) for ch in (text or "").lower()]


def _pl_collate(a: str, b: str) -> int:
    ka, kb = _pl_key(a), _pl_key(b)
    return (ka > kb) - (ka < kb)


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("pl_lower", 1, lambda s: s.lower() if isinstance(s, str) else s, deterministic=True)
    conn.create_collation("POLSKI", _pl_collate)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


#  walidacja
def _clean_text(value, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BookNestError(f"Pole „{label}” musi być tekstem.")
    return value.strip()


def normalize_isbn(raw: str | None) -> str | None:
    """Czyści ISBN i sprawdza cyfrę kontrolną."""
    raw = _clean_text(raw, "ISBN")
    s = re.sub(r"[\s-]", "", raw).upper()
    if not s:
        return None
    if not is_valid_isbn(s):
        raise BookNestError(
            f"Niepoprawny numer ISBN „{raw}”. Podaj ISBN-10 lub ISBN-13 z poprawną cyfrą kontrolną."
        )
    return s


def _isbn13_sum(number):
    total = 0
    for position, digit in enumerate(number):
        weight = 1 if position % 2 == 0 else 3
        total += int(digit) * weight
    return total


def _isbn10_sum(number):
    total = 0
    for position, digit in enumerate(number):
        value = 10 if digit == "X" else int(digit)
        total += value * (10 - position)
    return total


def is_valid_isbn(s: str) -> bool:
    if re.fullmatch(r"\d{13}", s):
        return _isbn13_sum(s) % 10 == 0
    if re.fullmatch(r"\d{9}[\dX]", s):
        return _isbn10_sum(s) % 11 == 0
    return False


def _equivalent_isbn(isbn: str) -> str | None:
    if len(isbn) == 10:
        prefix = "978" + isbn[:9]
        check_digit = (10 - _isbn13_sum(prefix) % 10) % 10
        return prefix + str(check_digit)
    if isbn.startswith("978"):
        prefix = isbn[3:-1]
        check_digit = (11 - _isbn10_sum(prefix) % 11) % 11
        return prefix + ("X" if check_digit == 10 else str(check_digit))
    return None


def _isbn_in_use(conn, isbn: str | None, exclude_book_id=None) -> bool:
    if isbn is None:
        return False
    return conn.execute(
        "SELECT 1 FROM Ksiazki WHERE isbn IN (?, ?) AND id_ksiazki IS NOT ? LIMIT 1",
        (isbn, _equivalent_isbn(isbn), exclude_book_id),
    ).fetchone() is not None


def _required(value: str | None, label: str, max_len: int) -> str:
    v = _clean_text(value, label)
    if not v:
        raise BookNestError(f"Pole „{label}” jest wymagane.")
    if len(v) > max_len:
        raise BookNestError(f"Pole „{label}” może mieć maksymalnie {max_len} znaków.")
    return v


def _year(value) -> int | None:
    if value is None:
        return None
    if type(value) not in (int, str):
        raise BookNestError("Rok wydania musi być liczbą całkowitą.")
    text = str(value).strip()
    if not text:
        return None
    try:
        y = int(text)
    except ValueError:
        raise BookNestError("Rok wydania musi być liczbą całkowitą.")
    max_year = min(2100, today().year + 1)
    if not 1400 <= y <= max_year:
        raise BookNestError(f"Rok wydania musi mieścić się w przedziale 1400–{max_year}.")
    return y


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def _email(value: str | None) -> str | None:
    v = _clean_text(value, "E-mail").lower()
    if not v:
        return None
    if len(v) > 100 or not _EMAIL_RE.match(v):
        raise BookNestError("Podaj poprawny adres email.")
    return v


def _phone(value: str | None) -> str | None:
    v = _clean_text(value, "Telefon")
    if not v:
        return None
    digits = re.sub(r"\D", "", v)
    if not re.fullmatch(r"\+?[\d\s-]+", v) or not 7 <= len(digits) <= 15:
        raise BookNestError("Telefon może zawierać tylko cyfry, spacje, myślniki i znak + (7–15 cyfr).")
    return v


def _parse_date(value, label: str) -> date:
    if isinstance(value, date):
        value = value.isoformat()
    value = _clean_text(value, label)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise BookNestError(f"Pole „{label}” musi być datą w formacie RRRR-MM-DD.")


def _to_int(value, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise BookNestError(f"Wybierz poprawną wartość w polu „{label}”.")


# książki
def _book_fields(title, author, isbn, year):
    return (
        _required(title, "Tytuł", 150),
        _required(author, "Autor", 100),
        normalize_isbn(isbn),
        _year(year),
    )


def add_book(conn, title, author, isbn=None, year=None) -> int:
    t, a, i, y = _book_fields(title, author, isbn, year)
    try:
        with conn:
            # Blokada zapisu obejmuje również sprawdzenie równoważnego ISBN.
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            if _isbn_in_use(conn, i):
                raise BookNestError(f"Książka o numerze ISBN {i} już istnieje w katalogu.")
            cur = conn.execute(
                "INSERT INTO Ksiazki (tytul, autor, isbn, rok_wydania) VALUES (?, ?, ?, ?)",
                (t, a, i, y),
            )
    except sqlite3.IntegrityError:
        raise BookNestError(f"Książka o numerze ISBN {i} już istnieje w katalogu.")
    return cur.lastrowid


def update_book(conn, book_id, title, author, isbn=None, year=None) -> None:
    t, a, i, y = _book_fields(title, author, isbn, year)
    try:
        with conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            book = get_book(conn, book_id)
            if i != book["isbn"] and _isbn_in_use(conn, i, book_id):
                raise BookNestError(f"Inna książka ma już ten numer ISBN {i}.")
            conn.execute(
                "UPDATE Ksiazki SET tytul=?, autor=?, isbn=?, rok_wydania=? WHERE id_ksiazki=?",
                (t, a, i, y, book_id),
            )
    except sqlite3.IntegrityError:
        raise BookNestError(f"Inna książka ma już ten numer ISBN {i}.")


def get_book(conn, book_id):
    row = conn.execute("SELECT * FROM Ksiazki WHERE id_ksiazki=?", (book_id,)).fetchone()
    if row is None:
        raise BookNestError("Nie znaleziono książki o podanym identyfikatorze.")
    return row


def delete_book(conn, book_id) -> None:
    book = get_book(conn, book_id)
    if book["status"] == STATUS_LOANED:
        raise BookNestError("Nie można usunąć książki, która jest aktualnie wypożyczona.")
    n = conn.execute("SELECT COUNT(*) FROM Wypozyczenia WHERE id_ksiazki=?", (book_id,)).fetchone()[0]
    if n:
        raise BookNestError("Nie można usunąć książki, która ma historię wypożyczeń.")
    with conn:
        conn.execute("DELETE FROM Ksiazki WHERE id_ksiazki=?", (book_id,))


def list_books(conn, query: str = "", status: str = ""):
    search_text = (query or "").strip().lower()
    isbn_fragment = re.sub(r"[\s-]", "", search_text).upper()
    equivalent_isbn = None
    if is_valid_isbn(isbn_fragment):
        equivalent_isbn = _equivalent_isbn(isbn_fragment)
    if not re.fullmatch(r"\d+X?", isbn_fragment):
        isbn_fragment = None
    if status not in (STATUS_AVAILABLE, STATUS_LOANED):
        status = None

    return conn.execute(
        """
        SELECT * FROM Ksiazki
        WHERE (:status IS NULL OR status = :status)
          AND (instr(pl_lower(tytul), :text) > 0 OR instr(pl_lower(autor), :text) > 0
               OR instr(isbn, :isbn) > 0 OR isbn = :equivalent)
        ORDER BY tytul COLLATE POLSKI, id_ksiazki
        """,
        {"status": status, "text": search_text, "isbn": isbn_fragment, "equivalent": equivalent_isbn},
    ).fetchall()


# czytelnicy
def _reader_fields(first, last, email, phone):
    return (
        _required(first, "Imię", 50),
        _required(last, "Nazwisko", 50),
        _email(email),
        _phone(phone),
    )


def add_reader(conn, first, last, email=None, phone=None) -> int:
    f, l, e, p = _reader_fields(first, last, email, phone)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO Czytelnicy (imie, nazwisko, email, telefon) VALUES (?, ?, ?, ?)",
                (f, l, e, p),
            )
    except sqlite3.IntegrityError:
        raise BookNestError("Ten adres e-mail jest już przypisany do innego czytelnika.")
    return cur.lastrowid


def update_reader(conn, reader_id, first, last, email=None, phone=None) -> None:
    get_reader(conn, reader_id)
    f, l, e, p = _reader_fields(first, last, email, phone)
    try:
        with conn:
            conn.execute(
                "UPDATE Czytelnicy SET imie=?, nazwisko=?, email=?, telefon=? WHERE id_czytelnika=?",
                (f, l, e, p, reader_id),
            )
    except sqlite3.IntegrityError:
        raise BookNestError("Ten adres e-mail jest już przypisany do innego czytelnika.")


def get_reader(conn, reader_id):
    row = conn.execute("SELECT * FROM Czytelnicy WHERE id_czytelnika=?", (reader_id,)).fetchone()
    if row is None:
        raise BookNestError("Nie znaleziono czytelnika o tym id")
    return row


def delete_reader(conn, reader_id) -> None:
    get_reader(conn, reader_id)
    n = conn.execute("SELECT COUNT(*) FROM Wypozyczenia WHERE id_czytelnika=?", (reader_id,)).fetchone()[0]
    if n:
        raise BookNestError("Nie można usunąć czytelnika, który ma wypożyczenia w historii.")
    with conn:
        conn.execute("DELETE FROM Czytelnicy WHERE id_czytelnika=?", (reader_id,))


def list_readers(conn):
    return conn.execute(
        """
        SELECT c.*,
               (SELECT COUNT(*) FROM Wypozyczenia w
                 WHERE w.id_czytelnika = c.id_czytelnika AND w.data_zwrotu IS NULL) AS aktywne,
               (SELECT COUNT(*) FROM Wypozyczenia w
                 WHERE w.id_czytelnika = c.id_czytelnika) AS razem
        FROM Czytelnicy c
        ORDER BY nazwisko COLLATE POLSKI, imie COLLATE POLSKI
        """
    ).fetchall()


# wypożyczenia
def default_due_date() -> date:
    return today() + timedelta(days=LOAN_DAYS)


def loan_book(conn, book_id, reader_id, due=None) -> int:
    book_id = _to_int(book_id, "Książka")
    reader_id = _to_int(reader_id, "Czytelnik")
    start = today()
    due_date = start + timedelta(days=LOAN_DAYS)
    if due:
        due_date = _parse_date(due, "Termin zwrotu")
    if due_date < start:
        raise BookNestError("Termin zwrotu nie może być wcześniejszy niż dzisiejsza data.")
    get_reader(conn, reader_id)
    book = get_book(conn, book_id)
    try:
        with conn:
            # zmiana statusu powiedzie się tylko, jeśli książka wciąż jest dostępna
            cur = conn.execute(
                "UPDATE Ksiazki SET status=? WHERE id_ksiazki=? AND status=?",
                (STATUS_LOANED, book_id, STATUS_AVAILABLE),
            )
            if cur.rowcount == 0:
                raise BookNestError(f"Książka „{book['tytul']}” jest już wypożyczona.")
            cur = conn.execute(
                "INSERT INTO Wypozyczenia (id_ksiazki, id_czytelnika, data_wypozyczenia, termin_zwrotu)"
                " VALUES (?, ?, ?, ?)",
                (book_id, reader_id, start.isoformat(), due_date.isoformat()),
            )
    except sqlite3.IntegrityError:
        raise BookNestError("Nie udało się zarejestrować wypożyczenia (naruszenie spójności danych).")
    return cur.lastrowid


def return_book(conn, loan_id) -> None:
    loan_id = _to_int(loan_id, "Wypożyczenie")
    loan = conn.execute("SELECT * FROM Wypozyczenia WHERE id_wypozyczenia=?", (loan_id,)).fetchone()
    if loan is None:
        raise BookNestError("Nie znaleziono takiego wypożyczenia.")
    if loan["data_zwrotu"] is not None:
        raise BookNestError("Ta książka została już zwrócona.")
    returned_on = today().isoformat()
    if returned_on < loan["data_wypozyczenia"]:
        raise BookNestError("Data zwrotu nie może być wcześniejsza niż data wypożyczenia.")
    with conn:
        cur = conn.execute(
            "UPDATE Wypozyczenia SET data_zwrotu=? WHERE id_wypozyczenia=? AND data_zwrotu IS NULL",
            (returned_on, loan_id),
        )
        if cur.rowcount == 0:
            raise BookNestError("Wypożyczenie zostało już zamknięte lub usunięte.")
        conn.execute(
            "UPDATE Ksiazki SET status=? WHERE id_ksiazki=?",
            (STATUS_AVAILABLE, loan["id_ksiazki"]),
        )


_LOAN_SELECT = """
    SELECT w.*, k.tytul, k.autor, c.imie, c.nazwisko,
           (w.data_zwrotu IS NULL AND w.termin_zwrotu < ?) AS po_terminie
    FROM Wypozyczenia w
    JOIN Ksiazki k   ON k.id_ksiazki = w.id_ksiazki
    JOIN Czytelnicy c ON c.id_czytelnika = w.id_czytelnika
"""


def list_loans(conn, active_only: bool = True):
    where = " WHERE w.data_zwrotu IS NULL" if active_only else ""
    order = " ORDER BY w.termin_zwrotu, w.id_wypozyczenia" if active_only else " ORDER BY w.id_wypozyczenia DESC"
    return conn.execute(_LOAN_SELECT + where + order, (today().isoformat(),)).fetchall()


def overdue_loans(conn):
    return [r for r in list_loans(conn, True) if r["po_terminie"]]


# statystyki
def stats(conn) -> dict:
    def count(sql, *params):
        return conn.execute(sql, params).fetchone()[0]

    return {
        "ksiazki": count("SELECT COUNT(*) FROM Ksiazki"),
        "dostepne": count("SELECT COUNT(*) FROM Ksiazki WHERE status=?", STATUS_AVAILABLE),
        "wypozyczone": count("SELECT COUNT(*) FROM Ksiazki WHERE status=?", STATUS_LOANED),
        "czytelnicy": count("SELECT COUNT(*) FROM Czytelnicy"),
        "po_terminie": count(
            "SELECT COUNT(*) FROM Wypozyczenia WHERE data_zwrotu IS NULL AND termin_zwrotu < ?",
            today().isoformat(),
        ),
        "wypozyczen_lacznie": count("SELECT COUNT(*) FROM Wypozyczenia"),
        "top_ksiazki": [
            dict(r)
            for r in conn.execute(
                """SELECT k.tytul, k.autor, COUNT(*) AS n FROM Wypozyczenia w
                   JOIN Ksiazki k ON k.id_ksiazki = w.id_ksiazki
                   GROUP BY k.id_ksiazki ORDER BY n DESC, k.tytul LIMIT 5"""
            )
        ],
        "top_czytelnicy": [
            dict(r)
            for r in conn.execute(
                """SELECT c.imie || ' ' || c.nazwisko AS osoba, COUNT(*) AS n FROM Wypozyczenia w
                   JOIN Czytelnicy c ON c.id_czytelnika = w.id_czytelnika
                   GROUP BY c.id_czytelnika ORDER BY n DESC, osoba LIMIT 5"""
            )
        ],
    }


# kopia zapasowa jsona
def export_data(conn) -> dict:
    def rows(sql):
        return [dict(row) for row in conn.execute(sql)]

    return {
        "wersja": EXPORT_VERSION,
        "wyeksportowano": today().isoformat(),
        "czytelnicy": rows("SELECT * FROM Czytelnicy ORDER BY id_czytelnika"),
        "ksiazki": rows("SELECT * FROM Ksiazki ORDER BY id_ksiazki"),
        "wypozyczenia": rows("SELECT * FROM Wypozyczenia ORDER BY id_wypozyczenia"),
    }


def _import_records(payload, section):
    records = payload.get(section)
    if not isinstance(records, list):
        raise BookNestError(f"Plik kopii nie zawiera poprawnej sekcji „{section}”.")
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise BookNestError(f"Sekcja „{section}”, rekord {index}: oczekiwano obiektu.")
    return records


def _import_id(record, field):
    value = record.get(field)
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise BookNestError(f"Pole „{field}” musi być dodatnim identyfikatorem całkowitym.")
    return value


def import_data(conn, payload) -> dict:
    """Zastępuje całą zawartość bazy danymi z kopii."""
    if (not isinstance(payload, dict) or type(payload.get("wersja")) is not int
            or payload["wersja"] != EXPORT_VERSION):
        raise BookNestError("Nieprawidłowy plik kopii zapasowej.")
    readers = _import_records(payload, "czytelnicy")
    books = _import_records(payload, "ksiazki")
    loans = _import_records(payload, "wypozyczenia")
    current_date = today()
    try:
        with conn:
            conn.execute("DELETE FROM Wypozyczenia")
            conn.execute("DELETE FROM Ksiazki")
            conn.execute("DELETE FROM Czytelnicy")
            for reader in readers:
                first_name, last_name, email, phone = _reader_fields(
                    reader.get("imie"), reader.get("nazwisko"), reader.get("email"), reader.get("telefon")
                )
                reader_id = _import_id(reader, "id_czytelnika")
                conn.execute(
                    "INSERT INTO Czytelnicy (id_czytelnika, imie, nazwisko, email, telefon) VALUES (?,?,?,?,?)",
                    (reader_id, first_name, last_name, email, phone),
                )
            for book in books:
                title, author, isbn, year = _book_fields(
                    book.get("tytul"), book.get("autor"), book.get("isbn"), book.get("rok_wydania")
                )
                book_id = _import_id(book, "id_ksiazki")
                conn.execute(
                    "INSERT INTO Ksiazki (id_ksiazki, tytul, autor, isbn, rok_wydania) VALUES (?,?,?,?,?)",
                    (book_id, title, author, isbn, year),
                )
            for loan in loans:
                start = _parse_date(loan.get("data_wypozyczenia"), "data_wypozyczenia")
                due = _parse_date(loan.get("termin_zwrotu"), "termin_zwrotu")
                if start > current_date:
                    raise BookNestError("Data wypożyczenia w kopii nie może być późniejsza niż dzisiaj.")
                returned_on = None
                if loan.get("data_zwrotu") is not None:
                    returned_date = _parse_date(loan["data_zwrotu"], "data_zwrotu")
                    if returned_date > current_date:
                        raise BookNestError("Data zwrotu w kopii nie może być późniejsza niż dzisiaj.")
                    returned_on = returned_date.isoformat()
                loan_id = _import_id(loan, "id_wypozyczenia")
                book_id = _import_id(loan, "id_ksiazki")
                reader_id = _import_id(loan, "id_czytelnika")
                conn.execute(
                    "INSERT INTO Wypozyczenia (id_wypozyczenia, id_ksiazki, id_czytelnika, data_wypozyczenia,"
                    " termin_zwrotu, data_zwrotu) VALUES (?,?,?,?,?,?)",
                    (loan_id, book_id, reader_id, start.isoformat(), due.isoformat(), returned_on),
                )
            # Status książek wynika z aktywnych wypozyczeń
            conn.execute(
                "UPDATE Ksiazki SET status=? WHERE id_ksiazki IN"
                " (SELECT id_ksiazki FROM Wypozyczenia WHERE data_zwrotu IS NULL)",
                (STATUS_LOANED,),
            )
    except sqlite3.IntegrityError as exc:
        raise BookNestError(f"Plik kopii zawiera niespójne dane: {exc}")
    except UnicodeError:
        raise BookNestError("Plik kopii zawiera niepoprawne znaki Unicode.")
    return {"czytelnicy": len(readers), "ksiazki": len(books), "wypozyczenia": len(loans)}
