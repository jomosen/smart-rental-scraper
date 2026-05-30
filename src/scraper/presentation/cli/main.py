import asyncio
import logging
import sys
from datetime import datetime, timedelta
from functools import partial

from dotenv import load_dotenv
load_dotenv()

from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.session import super_session
from src.scraper.presentation.cli.container import build_container


class _ColorFormatter(logging.Formatter):
    _BLUE   = "\033[34m"
    _GREEN  = "\033[32m"
    _YELLOW = "\033[33m"
    _RED    = "\033[31m"
    _RESET  = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if record.levelno >= logging.ERROR:
            return self._RED + msg + self._RESET
        if record.levelno >= logging.WARNING:
            return self._YELLOW + msg + self._RESET
        if record.levelno == logging.INFO:
            text = record.getMessage()
            if "Phase" in text:
                return self._BLUE + msg + self._RESET
            if "done in" in text:
                return self._GREEN + msg + self._RESET
        return msg


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_ColorFormatter(
    fmt="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
))
logging.basicConfig(level=logging.INFO, handlers=[_handler])

# --- Scraping period ---
PERIOD_DAYS = 300 
PERIOD_OFFSET_DAYS = 2
PICKUP_HOUR = 10

_now = datetime.now().replace(hour=PICKUP_HOUR, minute=0, second=0, microsecond=0)
PERIOD_START = _now + timedelta(days=PERIOD_OFFSET_DAYS)
PERIOD_END   = _now + timedelta(days=PERIOD_DAYS + PERIOD_OFFSET_DAYS)


async def main() -> None:
    start_time = datetime.now()
    print(
        f"--- Smart Rental Scraper "
        f"({PERIOD_START.strftime('%d/%m/%Y')} → {PERIOD_END.strftime('%d/%m/%Y')}) ---"
    )
    print(f"Start: {start_time.strftime('%d/%m/%Y %H:%M:%S')}")

    engine = super_engine()
    session_factory = partial(super_session, engine)

    container = build_container(
        pickup_hour=PICKUP_HOUR,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        session_factory=session_factory,
    )
    logging.info(
        "Catalog loaded from DB: %d orchestrator(s) scheduled.",
        len(container.orchestrators),
    )

    try:
        results = await asyncio.gather(*[
            orch.run(provider, pickup, dropoff, PERIOD_START, PERIOD_END)
            for provider, pickup, dropoff, orch in container.orchestrators
        ], return_exceptions=True)

        total_zones = total_inserted = total_skipped = 0
        for r in results:
            if isinstance(r, Exception):
                logging.error("Provider run failed: %s", r)
            else:
                total_zones += r.zones_detected
                total_inserted += r.observations_inserted
                total_skipped += r.observations_skipped

        print(
            f"\nAll providers done — zones={total_zones} "
            f"inserted={total_inserted} skipped={total_skipped}"
        )

    finally:
        end_time = datetime.now()
        elapsed = int((end_time - start_time).total_seconds())
        print(f"\n--- Finished ---")
        print(f"End     : {end_time.strftime('%d/%m/%Y %H:%M:%S')}")
        print(f"Duration: {elapsed // 3600:02d}h {(elapsed % 3600) // 60:02d}m {elapsed % 60:02d}s")


if __name__ == "__main__":
    asyncio.run(main())
