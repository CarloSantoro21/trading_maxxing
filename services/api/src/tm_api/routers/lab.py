"""Reportes del laboratorio de walk-forward (solo lectura) para la página /backtests (#24).

Los reportes son de la app, no de un usuario (capa compartida): cualquier usuario
autenticado los lee. Ver :mod:`tm_api.lab_reports`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request

from tm_api.auth import User
from tm_api.deps import not_found
from tm_api.lab_reports import REPORT_NAME, ReportNotFound, detail, list_names, load_report
from tm_api.lab_reports import reports_dir as _reports_dir
from tm_api.lab_reports import summary as _summary
from tm_api.schemas import LabReportDetail, LabReportSummary

router = APIRouter(prefix="/lab", tags=["lab"])

_ERRORS = {401: {"description": "Token ausente o inválido"}}


def _dir(request: Request):
    return _reports_dir(request.app.state.settings.lab_reports_dir)


@router.get(
    "/reports",
    response_model=list[LabReportSummary],
    summary="Listar los reportes del laboratorio",
    responses=_ERRORS,
)
def list_reports(request: Request, user: User) -> list[dict[str, Any]]:
    directory = _dir(request)
    out = []
    for name in list_names(directory):
        try:
            out.append(_summary(name, load_report(directory, name)))
        except (ReportNotFound, ValueError):
            # Un archivo ilegible no tumba la lista.
            continue
    return out


@router.get(
    "/reports/{name}",
    response_model=LabReportDetail,
    summary="Ver un reporte del laboratorio",
    description=(
        "Curva de equity out-of-sample contra comprar y mantener, tabla por ventana del "
        "walk-forward, histograma del drawdown máximo del Monte Carlo (con su p95) y el "
        "veredicto con sus razones."
    ),
    responses={**_ERRORS, 404: {"description": "No existe el reporte"}},
)
def get_report(
    request: Request,
    user: User,
    name: Annotated[str, Path(pattern=REPORT_NAME.pattern, description="p. ej. trend")],
) -> dict[str, Any]:
    try:
        return detail(_dir(request), name)
    except ReportNotFound as exc:
        raise not_found("Reporte") from exc
