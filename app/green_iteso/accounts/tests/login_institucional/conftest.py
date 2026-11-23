"""Reporte de hallazgos de las pruebas del login institucional.

Referencia: ID01 ("Identidad 1" en la división de trabajo del equipo) = login
institucional con Microsoft (mock y Entra ID), dominio @iteso.mx, alta o
actualización del usuario y rol según el puesto.

Cada prueba marcada con ``@pytest.mark.hallazgo(...)`` describe un problema
encontrado: la prueba afirma el comportamiento correcto y va con
``xfail(strict=True)``. Mientras el problema exista la prueba sale como
"xfailed" (pendiente). Cuando alguien lo arregle, la prueba pasa, pytest la
marca como fallida por ``strict`` y el reporte dice "resuelto": solo hay que
quitar el ``xfail``.

Para generar el reporte en Markdown:

    HALLAZGOS_REPORT=docs/pruebas/hallazgos-id01-back.md \
        pytest app/green_iteso/accounts/tests/login_institucional
"""

from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from django.core.cache import caches
from django.test import override_settings

_SEVERITY_ORDER = {"alta": 0, "media": 1, "baja": 2}


@dataclass
class _Hallazgo:
    titulo: str
    integrante: str
    severidad: str
    detalle: str
    recomendacion: str
    prueba: str
    estado: str = "sin correr"


_HALLAZGOS: dict[str, _Hallazgo] = {}


@pytest.fixture
def modo_mock() -> Generator[None, None, None]:
    """Login en modo mock, dominio @iteso.mx y contadores de throttling limpios."""
    with override_settings(
        MICROSOFT_AUTH_MODE="mock",
        MICROSOFT_TENANT_ID="",
        MICROSOFT_CLIENT_ID="",
        ALLOWED_EMAIL_DOMAIN="iteso.mx",
        STAFF_EMAILS=[],
    ):
        caches["default"].clear()
        caches["throttle"].clear()
        yield


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "hallazgo(titulo, integrante, severidad, detalle, recomendacion): "
        "problema encontrado por las pruebas del login institucional (ID01)",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        marker = item.get_closest_marker("hallazgo")
        if marker is not None:
            _HALLAZGOS[item.nodeid] = _Hallazgo(prueba=item.nodeid, **marker.kwargs)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):  # noqa: ANN201
    outcome = yield
    report = outcome.get_result()
    hallazgo = _HALLAZGOS.get(item.nodeid)
    if hallazgo is None or report.when != "call":
        return
    if hasattr(report, "wasxfail"):
        hallazgo.estado = "pendiente"
    elif report.failed:
        # Con strict=True una prueba "xfail" que pasa se reporta como fallida.
        hallazgo.estado = "resuelto (quitar el xfail)"
    else:
        hallazgo.estado = "resuelto"
    del call


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if not _HALLAZGOS:
        return
    terminalreporter.section("Hallazgos ID01: login institucional")
    for hallazgo in _ordered():
        terminalreporter.write_line(
            f"[{hallazgo.severidad.upper()}] {hallazgo.estado:<26} "
            f"{hallazgo.integrante}: {hallazgo.titulo}"
        )
    path = os.environ.get("HALLAZGOS_REPORT")
    if path:
        _write_markdown(Path(path))
        terminalreporter.write_line(f"Reporte escrito en {path}")


def _ordered() -> list[_Hallazgo]:
    return sorted(
        _HALLAZGOS.values(),
        key=lambda h: (_SEVERITY_ORDER.get(h.severidad, 9), h.integrante, h.titulo),
    )


def _write_markdown(path: Path) -> None:
    hallazgos = _ordered()
    lines = [
        "# Hallazgos ID01: login institucional (backend)",
        "",
        "ID01 = Identidad 1 en la división de trabajo: login con Microsoft (mock y "
        "Entra ID), dominio @iteso.mx, alta o actualización del usuario y rol por puesto.",
        "",
        f"Generado por `pytest` el {date.today().isoformat()}. Cada hallazgo tiene una",
        "prueba que afirma el comportamiento correcto y se marca `xfail` mientras el",
        "problema siga ahí.",
        "",
        "## Resumen por integrante",
        "",
        "| Integrante | Pendientes | Resueltos |",
        "|---|---|---|",
    ]
    for integrante in sorted({h.integrante for h in hallazgos}):
        mine = [h for h in hallazgos if h.integrante == integrante]
        pending = sum(h.estado == "pendiente" for h in mine)
        lines.append(f"| {integrante} | {pending} | {len(mine) - pending} |")
    lines += ["", "## Detalle", ""]
    for number, h in enumerate(hallazgos, start=1):
        lines += [
            f"### {number}. {h.titulo}",
            "",
            f"- **Integrante:** {h.integrante}",
            f"- **Severidad:** {h.severidad}",
            f"- **Estado:** {h.estado}",
            f"- **Qué pasa:** {h.detalle}",
            f"- **Qué hacer:** {h.recomendacion}",
            f"- **Prueba:** `{h.prueba}`",
            "",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
