"""Demo CLI for PriceQueryService — one of the three Format A queries."""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date

from dotenv import load_dotenv
from sqlalchemy import func, select

from ...infrastructure.persistence.engine import app_engine
from ...infrastructure.persistence.models.tenant import (
    Tenant,
    TenantSubscription,
    TenantVehicleGroup,
)
from ...infrastructure.persistence.session import make_session_factory, tenant_context
from ..price_query.service import PriceQueryService
from .formatter import format_table

_DEFAULT_DURATIONS = "1,2,3,4,5,6,7,14,21,28"


class _CliError(Exception):
    pass


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Consulta el tarifario de mercado de un tenant.",
        add_help=True,
    )
    p.add_argument("--tenant-id", required=True, metavar="UUID", help="UUID del tenant")
    p.add_argument(
        "--query",
        required=True,
        choices=["provider", "average", "minimum"],
        help="Tipo de consulta",
    )
    p.add_argument(
        "--client-groups",
        required=True,
        metavar="B,C,SUV",
        help="Lista de códigos de grupo separados por comas",
    )
    p.add_argument(
        "--date-range",
        required=True,
        metavar="YYYY-MM-DD:YYYY-MM-DD",
        help="Rango de fechas, inicio:fin inclusive",
    )
    p.add_argument(
        "--durations",
        default=_DEFAULT_DURATIONS,
        metavar="1,3,7,14,28",
        help="Duraciones en días separadas por comas",
    )
    p.add_argument("--provider", default=None, help="Código del proveedor (solo --query=provider)")
    p.add_argument("--location", default=None, help="Código de ubicación (solo --query=provider)")
    p.add_argument("--rate", default=None, help="Código de tarifa (solo --query=provider)")
    return p.parse_args(argv)


def _validate(
    args: argparse.Namespace,
) -> tuple[uuid.UUID, tuple[date, date], list[str], list[int]]:
    # 1. tenant-id
    try:
        tenant_id = uuid.UUID(args.tenant_id)
    except (ValueError, AttributeError):
        raise _CliError(f"--tenant-id no es un UUID válido: {args.tenant_id!r}")

    # 2. --query validated by argparse choices

    # 3. date-range
    try:
        raw = args.date_range.split(":")
        if len(raw) != 2:
            raise ValueError
        d1 = date.fromisoformat(raw[0].strip())
        d2 = date.fromisoformat(raw[1].strip())
    except (ValueError, TypeError):
        raise _CliError(
            f"--date-range debe tener formato YYYY-MM-DD:YYYY-MM-DD, "
            f"recibido: {args.date_range!r}"
        )
    if d1 > d2:
        raise _CliError(
            f"--date-range inválido: la fecha inicial ({d1}) es posterior a la final ({d2})"
        )

    # 4. client-groups
    groups = [g.strip() for g in args.client_groups.split(",") if g.strip()]
    if not groups:
        raise _CliError("--client-groups no puede estar vacío")

    # 5. durations
    try:
        durations = [int(d.strip()) for d in args.durations.split(",")]
        if any(d <= 0 for d in durations):
            raise ValueError("duraciones deben ser positivas")
    except (ValueError, TypeError):
        raise _CliError(
            f"--durations debe ser una lista de enteros positivos, "
            f"recibido: {args.durations!r}"
        )

    # 6. provider/location/rate required for query=provider
    if args.query == "provider":
        missing = [
            name
            for name, val in [
                ("--provider", args.provider),
                ("--location", args.location),
                ("--rate", args.rate),
            ]
            if not val
        ]
        if missing:
            raise _CliError(f"--query=provider requiere {', '.join(missing)}")

    # 7. warn if provider/location/rate passed with average or minimum
    elif args.provider or args.location or args.rate:
        print(
            "Advertencia: ignorados: --provider/--location/--rate "
            "solo se usan con --query=provider",
            file=sys.stderr,
        )

    return tenant_id, (d1, d2), groups, durations


def main(argv=None) -> int:
    load_dotenv()
    try:
        return _run(argv)
    except _CliError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error inesperado: {e}", file=sys.stderr)
        return 1


def _run(argv=None) -> int:
    args = _parse_args(argv)
    tenant_id, date_range, client_groups, durations = _validate(args)

    engine = app_engine()
    factory = make_session_factory(engine)

    with tenant_context(factory, tenant_id) as session:
        tenant = session.get(Tenant, tenant_id)
        tenant_name = tenant.name if tenant else str(tenant_id)

        service = PriceQueryService(session)

        if args.query == "provider":
            result = service.get_provider_tariff(
                tenant_id,
                args.provider,
                args.location,
                args.rate,
                date_range,
                client_groups,
                durations,
            )
        elif args.query == "average":
            result = service.get_market_average_tariff(
                tenant_id, date_range, client_groups, durations
            )
        else:
            result = service.get_market_minimum_tariff(
                tenant_id, date_range, client_groups, durations
            )

        sub_count = session.scalar(
            select(func.count()).select_from(TenantSubscription).where(
                TenantSubscription.tenant_id == tenant_id,
                TenantSubscription.status == "active",
            )
        ) or 0

        groups_orm = session.scalars(
            select(TenantVehicleGroup).where(
                TenantVehicleGroup.tenant_id == tenant_id,
                TenantVehicleGroup.code.in_(client_groups),
            )
        ).all()
        group_names = {g.code: g.name for g in groups_orm}

    extra_context: dict = {
        "num_subscriptions": sub_count,
        "group_names": group_names,
    }
    if args.query == "provider":
        extra_context["provider"] = args.provider or ""
        extra_context["location"] = args.location or ""
        extra_context["rate"] = args.rate or ""

    output = format_table(result, args.query, tenant_name, extra_context)
    print(output, end="")
    return 0
