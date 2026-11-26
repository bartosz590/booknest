import json
import sqlite3
from copy import deepcopy
from datetime import timedelta
from io import BytesIO

import pytest

import services as svc


DUNE_ISBN = "9788375780635"


def test_TC01_bledna_cyfra_isbn():
    with pytest.raises(svc.BookNestError, match="Niepoprawny numer ISBN"):
        svc.normalize_isbn("9788375780636")


def test_TC02_powtorzony_isbn(conn):
    svc.add_book(conn, "Diuna", "Frank Herbert", DUNE_ISBN)

    with pytest.raises(svc.BookNestError, match="już istnieje"):
        svc.add_book(conn, "Druga Diuna", "Frank Herbert", "978-83-7578-063-5")

    assert len(svc.list_books(conn)) == 1


def test_TC03_wyszukiwanie_z_polskimi_znakami(conn):
    svc.add_book(conn, "Żeglarz", "Łukasz Żak")
    svc.add_book(conn, "Diuna", "Frank Herbert")

    books = svc.list_books(conn, "żak")

    assert [book["tytul"] for book in books] == ["Żeglarz"]


@pytest.fixture
def library_client(client):
    book = client.post("/ksiazki/nowa", data={"tytul": "Diuna", "autor": "Frank Herbert"})
    reader = client.post("/czytelnicy/nowy", data={"imie": "Anna", "nazwisko": "Kowalska"})
    assert book.status_code == reader.status_code == 302
    return client


def test_TC04_wypozyczenie_i_zwrot(library_client):
    client = library_client
    form = {"id_ksiazki": "1", "id_czytelnika": "1"}

    assert client.post("/wypozyczenia/nowe", data=form).status_code == 302
    export = client.get("/kopia/eksport").get_json()
    loan = export["wypozyczenia"][0]
    assert export["ksiazki"][0]["status"] == "Wypożyczona"
    assert loan["termin_zwrotu"] == (svc.today() + timedelta(days=30)).isoformat()

    second = client.post("/wypozyczenia/nowe", data=form)
    assert second.status_code == 400
    assert "już wypożyczona" in second.text
    assert len(client.get("/kopia/eksport").get_json()["wypozyczenia"]) == 1

    assert client.post(f"/wypozyczenia/{loan['id_wypozyczenia']}/zwrot").status_code == 302
    export = client.get("/kopia/eksport").get_json()
    assert export["ksiazki"][0]["status"] == "Dostępna"
    assert export["wypozyczenia"][0]["data_zwrotu"] == svc.today().isoformat()


def test_TC05_baza_blokuje_drugie_wypozyczenie(conn):
    book_id = svc.add_book(conn, "Diuna", "Frank Herbert")
    reader_id = svc.add_reader(conn, "Anna", "Kowalska")
    svc.loan_book(conn, book_id, reader_id)
    today = svc.today().isoformat()

    with pytest.raises(sqlite3.IntegrityError, match=r"UNIQUE constraint failed: Wypozyczenia\.id_ksiazki"):
        conn.execute(
            "INSERT INTO Wypozyczenia (id_ksiazki, id_czytelnika, data_wypozyczenia, termin_zwrotu)"
            " VALUES (?, ?, ?, ?)",
            (book_id, reader_id, today, today),
        )


def test_TC06_eksport_i_przywrocenie_kopii(library_client):
    client = library_client
    form = {"id_ksiazki": "1", "id_czytelnika": "1"}
    assert client.post("/wypozyczenia/nowe", data=form).status_code == 302
    backup = client.get("/kopia/eksport")
    book = client.post("/ksiazki/nowa", data={"tytul": "Solaris", "autor": "Stanisław Lem"})
    assert book.status_code == 302

    upload = (BytesIO(backup.data), "kopia.json")
    response = client.post("/kopia/import", data={"plik": upload}, follow_redirects=True)

    assert "Przywrócono dane" in response.text
    assert client.get("/kopia/eksport").get_json() == backup.get_json()


def test_TC07_bledny_tytul_w_kopii(library_client):
    client = library_client
    before = client.get("/kopia/eksport").get_json()
    backup = deepcopy(before)
    backup["ksiazki"][0]["tytul"] = 123
    upload = BytesIO(json.dumps(backup).encode("utf-8"))

    response = client.post("/kopia/import", data={"plik": (upload, "kopia.json")}, follow_redirects=True)

    assert response.status_code == 200
    assert "musi być tekstem" in response.text
    assert client.get("/kopia/eksport").get_json() == before


def test_TC08_kopia_z_przyszla_data(library_client):
    client = library_client
    form = {"id_ksiazki": "1", "id_czytelnika": "1"}
    assert client.post("/wypozyczenia/nowe", data=form).status_code == 302
    before = client.get("/kopia/eksport").get_json()
    backup = deepcopy(before)
    tomorrow = (svc.today() + timedelta(days=1)).isoformat()
    backup["wypozyczenia"][0]["data_wypozyczenia"] = tomorrow
    upload = BytesIO(json.dumps(backup).encode("utf-8"))

    response = client.post("/kopia/import", data={"plik": (upload, "kopia.json")}, follow_redirects=True)

    assert response.status_code == 200
    assert "Data wypożyczenia w kopii" in response.text
    assert client.get("/kopia/eksport").get_json() == before
    loan_id = before["wypozyczenia"][0]["id_wypozyczenia"]
    response = client.post(f"/wypozyczenia/{loan_id}/zwrot", follow_redirects=True)
    assert "Zwrot został odnotowany" in response.text
