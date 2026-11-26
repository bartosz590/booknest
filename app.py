"""BookNest - aplikacja webowa do zarządzania domową biblioteką."""
from __future__ import annotations

import json
import os
import secrets
from contextlib import closing
from datetime import date

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, session, url_for)

import services as svc


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("BOOKNEST_DB", "booknest.db"),
        SECRET_KEY=os.environ.get("BOOKNEST_SECRET") or secrets.token_hex(32),
        CSRF_ENABLED=True,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    if config:
        app.config.update(config)

    with closing(svc.connect(app.config["DATABASE"])) as conn:
        svc.init_db(conn)

    # infrastruktura
    def db():
        if "db" not in g:
            g.db = svc.connect(app.config["DATABASE"])
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.before_request
    def csrf_protect():
        if request.method == "POST" and app.config["CSRF_ENABLED"]:
            sent = request.form.get("csrf_token", "")
            if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
                abort(400, description="Nieprawidłowy lub brakujący token CSRF.")

    @app.context_processor
    def inject_globals():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"csrf_token": session["csrf"], "dzis": svc.today().isoformat()}

    @app.template_filter("data")
    def fmt_date(value):
        if not value:
            return "—"
        return date.fromisoformat(str(value)).strftime("%d.%m.%Y")

    @app.errorhandler(400)
    def bad_request(err):
        return render_template("error.html", kod=400, opis=err.description), 400

    @app.errorhandler(404)
    def not_found(err):
        return render_template("error.html", kod=404, opis="Nie znaleziono strony."), 404

    @app.errorhandler(413)
    def too_large(err):
        return render_template("error.html", kod=413, opis="Plik jest za duży (limit 2 MB)."), 413

    # panel
    @app.route("/")
    def dashboard():
        conn = db()
        return render_template(
            "dashboard.html",
            s=svc.stats(conn),
            polka=svc.list_books(conn)[:70],
            aktywne=svc.list_loans(conn, True)[:6],
        )

    # książki
    @app.route("/ksiazki")
    def books():
        q = request.args.get("q", "")
        status = request.args.get("status", "")
        return render_template("books.html", ksiazki=svc.list_books(db(), q, status), q=q, status=status)

    @app.route("/ksiazki/nowa", methods=["GET", "POST"], endpoint="book_new")
    @app.route("/ksiazki/<int:book_id>/edytuj", methods=["GET", "POST"], endpoint="book_edit")
    def book_form(book_id=None):
        book = None
        form = {"tytul": "", "autor": "", "isbn": "", "rok_wydania": ""}
        editing = book_id is not None
        if editing:
            try:
                book = svc.get_book(db(), book_id)
            except svc.BookNestError:
                abort(404)
            for field in form:
                form[field] = book[field] or ""

        if request.method != "POST":
            return render_template("book_form.html", form=form, edit=editing, book=book)

        for field in form:
            form[field] = request.form.get(field, "")
        title = form["tytul"]
        author = form["autor"]
        isbn = form["isbn"]
        year = form["rok_wydania"]
        try:
            if editing:
                svc.update_book(db(), book_id, title, author, isbn, year)
                flash("Zapisano zmiany w książce.", "success")
            else:
                svc.add_book(db(), title, author, isbn, year)
                flash(f"Dodano książkę „{title.strip()}”.", "success")
        except svc.BookNestError as error:
            flash(str(error), "danger")
            return render_template("book_form.html", form=form, edit=editing, book=book), 400
        return redirect(url_for("books"))

    @app.post("/ksiazki/<int:book_id>/usun")
    def book_delete(book_id):
        try:
            svc.delete_book(db(), book_id)
            flash("Książka została usunięta z katalogu.", "success")
        except svc.BookNestError as e:
            flash(str(e), "danger")
        return redirect(url_for("books"))

    # czytelnicy
    @app.route("/czytelnicy")
    def readers():
        return render_template("readers.html", czytelnicy=svc.list_readers(db()))

    @app.route("/czytelnicy/nowy", methods=["GET", "POST"], endpoint="reader_new")
    @app.route("/czytelnicy/<int:reader_id>/edytuj", methods=["GET", "POST"], endpoint="reader_edit")
    def reader_form(reader_id=None):
        form = {"imie": "", "nazwisko": "", "email": "", "telefon": ""}
        editing = reader_id is not None
        if editing:
            try:
                reader = svc.get_reader(db(), reader_id)
            except svc.BookNestError:
                abort(404)
            for field in form:
                form[field] = reader[field] or ""

        if request.method != "POST":
            return render_template("reader_form.html", form=form, edit=editing)

        for field in form:
            form[field] = request.form.get(field, "")
        first_name = form["imie"]
        last_name = form["nazwisko"]
        email = form["email"]
        phone = form["telefon"]
        try:
            if editing:
                svc.update_reader(db(), reader_id, first_name, last_name, email, phone)
                flash("Zapisano zmiany danych czytelnika.", "success")
            else:
                svc.add_reader(db(), first_name, last_name, email, phone)
                flash(f"Zarejestrowano czytelnika: {first_name.strip()} {last_name.strip()}.", "success")
        except svc.BookNestError as error:
            flash(str(error), "danger")
            return render_template("reader_form.html", form=form, edit=editing), 400
        return redirect(url_for("readers"))

    @app.post("/czytelnicy/<int:reader_id>/usun")
    def reader_delete(reader_id):
        try:
            svc.delete_reader(db(), reader_id)
            flash("Czytelnik został usunięty.", "success")
        except svc.BookNestError as e:
            flash(str(e), "danger")
        return redirect(url_for("readers"))

    # wypożyczenia
    @app.route("/wypozyczenia")
    def loans():
        widok = request.args.get("widok", "aktywne")
        return render_template("loans.html", widok=widok,
                               wypozyczenia=svc.list_loans(db(), widok != "historia"))

    @app.route("/wypozyczenia/nowe", methods=["GET", "POST"])
    def loan_new():
        conn = db()
        form = {"id_ksiazki": request.args.get("ksiazka", ""), "id_czytelnika": "",
                "termin_zwrotu": svc.default_due_date().isoformat()}
        if request.method == "POST":
            form = {k: request.form.get(k, "") for k in form}
            try:
                svc.loan_book(conn, form["id_ksiazki"], form["id_czytelnika"], form["termin_zwrotu"])
            except svc.BookNestError as e:
                flash(str(e), "danger")
            else:
                flash("Wypożyczenie zostało zarejestrowane.", "success")
                return redirect(url_for("loans"))
        return render_template(
            "loan_form.html", form=form,
            dostepne=svc.list_books(conn, status=svc.STATUS_AVAILABLE),
            czytelnicy=svc.list_readers(conn),
        ), (400 if request.method == "POST" else 200)

    @app.post("/wypozyczenia/<int:loan_id>/zwrot")
    def loan_return(loan_id):
        try:
            svc.return_book(db(), loan_id)
            flash("Zwrot został odnotowany.", "success")
        except svc.BookNestError as e:
            flash(str(e), "danger")
        nxt = request.form.get("next", "")
        if nxt not in {url_for("loans", widok="aktywne"), url_for("loans", widok="historia")}:
            nxt = url_for("loans")
        return redirect(nxt)

    # kopia zapasowa
    @app.route("/kopia")
    def backup():
        return render_template("backup.html")

    @app.route("/kopia/eksport")
    def backup_export():
        payload = json.dumps(svc.export_data(db()), ensure_ascii=False, indent=2)
        name = f"booknest-kopia-{svc.today().isoformat()}.json"
        return Response(payload, mimetype="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/kopia/import")
    def backup_import():
        f = request.files.get("plik")
        if not f or not f.filename:
            flash("Wybierz plik kopii zapasowej (.json).", "danger")
            return redirect(url_for("backup"))
        try:
            data = json.loads(f.read().decode("utf-8"))
        except (ValueError, RecursionError):
            flash("Plik nie jest poprawnym plikiem JSON.", "danger")
            return redirect(url_for("backup"))
        try:
            n = svc.import_data(db(), data)
        except svc.BookNestError as e:
            flash(str(e), "danger")
        else:
            flash(f"Przywrócono dane: {n['ksiazki']} książek, {n['czytelnicy']} czytelników, "
                  f"{n['wypozyczenia']} wypożyczeń.", "success")
        return redirect(url_for("backup"))

    return app


if __name__ == "__main__":
    create_app().run(debug=False, port=5000)
