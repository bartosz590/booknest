import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module
import services as svc


@pytest.fixture()
def conn(tmp_path):
    c = svc.connect(str(tmp_path / "test.db"))
    svc.init_db(c)
    yield c
    c.close()


@pytest.fixture()
def client(tmp_path):
    a = app_module.create_app({"DATABASE": str(tmp_path / "web.db"), "TESTING": True, "CSRF_ENABLED": False})
    return a.test_client()
