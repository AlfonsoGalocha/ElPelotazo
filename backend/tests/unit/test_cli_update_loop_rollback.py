"""Bug real reportado por el usuario: un fallo de verdad a mitad de una
iteracion del bucle de `cli.py update()` (en produccion fue Postgres
cerrando la conexion; aqui se reproduce de forma determinista con una
violacion de UNIQUE) dejaba la sesion de SQLAlchemy en estado invalido --
sin un `db.rollback()` antes de la SIGUIENTE iteracion, esa y TODAS las
posteriores (de esa competicion Y de las demas) fallaban en cascada con
`PendingRollbackError`, aunque el problema real solo afectara a UNA. Este
test prueba el mecanismo exacto del que depende el arreglo: una sesion
puede seguir usandose despues de un error solo si se hace `rollback()`
antes de la siguiente operacion."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition


def test_session_is_unusable_after_an_error_until_rolled_back():
    with session_scope() as db:
        db.add(Competition(code="rollback_test_dup", name="A", country="Testland"))
        db.flush()
        db.add(Competition(code="rollback_test_dup", name="B", country="Testland"))  # mismo code -> UNIQUE
        with pytest.raises(IntegrityError):
            db.flush()

        # Reproduce el bug real: sin rollback, CUALQUIER operacion
        # posterior en la MISMA sesion sigue fallando, aunque no tenga
        # nada que ver con el error original.
        db.add(Competition(code="rollback_test_unrelated", name="C", country="Testland"))
        with pytest.raises(Exception):  # noqa: B017 - PendingRollbackError (o equivalente), no IntegrityError
            db.flush()
        db.rollback()


def test_session_recovers_for_later_work_after_rollback():
    with session_scope() as db:
        db.add(Competition(code="rollback_test_dup2", name="A", country="Testland"))
        db.flush()
        db.add(Competition(code="rollback_test_dup2", name="B", country="Testland"))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()  # el arreglo real: justo esto, antes de seguir con la siguiente iteracion

        # La MISMA sesion vuelve a funcionar con normalidad para trabajo
        # NO relacionado con el error -- exactamente lo que necesita el
        # bucle de `update()`/`update-fixtures`/`update-odds` para que un
        # fallo en una (competicion, temporada) no tumbe las demas.
        db.add(Competition(code="rollback_test_recovered", name="D", country="Testland"))
        db.flush()

    with session_scope() as db:
        recovered = db.query(Competition).filter_by(code="rollback_test_recovered").one_or_none()
        assert recovered is not None
