import asyncio
import logging
import random
from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv()

from src.application.exporters.gap_exporter import GapExporter
from src.application.exporters.result_exporter import ResultExporter
from src.application.exporters.season_exporter import SeasonExporter
from src.application.filters.rate_filter import RateFilter
from src.application.models.search_request import SearchRequest
from src.application.services.result_expander import ResultExpander
from src.application.services.spot_check_service import SpotCheckService
from src.application.smart_scraping.smart_orchestrator import _DEFAULT_EXTRACTION_DURATIONS
from src.presentation.cli.container import build_container

logging.basicConfig(level=logging.INFO, format="%(message)s")

# --- Scraping period ---
PERIOD_DAYS = 90
PERIOD_OFFSET_DAYS = 2
PICKUP_HOUR = 10
SPOT_CHECK_COUNT = 10

_now = datetime.now().replace(hour=PICKUP_HOUR, minute=0, second=0, microsecond=0)
PERIOD_START = _now + timedelta(days=PERIOD_OFFSET_DAYS)
PERIOD_END   = _now + timedelta(days=PERIOD_DAYS + PERIOD_OFFSET_DAYS)


async def main() -> None:
    container = build_container(
        pickup_hour=PICKUP_HOUR,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
    )

    start_time = datetime.now()
    print(f"--- Starting Smart Scraper Engine ({PERIOD_START.strftime('%d/%m/%Y')} → {PERIOD_END.strftime('%d/%m/%Y')}) ---")
    print(f"Start : {start_time.strftime('%d/%m/%Y %H:%M:%S')}")

    try:
        # ── Run all providers in parallel ─────────────────────────────
        results = await asyncio.gather(*[
            orch.run(provider, pickup, dropoff, PERIOD_START, PERIOD_END)
            for provider, pickup, dropoff, orch in container.orchestrators
        ])

        # pairs: List[Tuple[SearchRequest, BookingResult]]  (from orchestrators)
        pairs = [pair for result in results for pair in result.pairs]
        provider_zones = [(r.provider_name, r.zones) for r in results]

        # Convert to domain-only pairs for exporters and expander
        bs_pairs = [(req.search, res) for req, res in pairs]

        # ── Export raw results ─────────────────────────────────────────
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ResultExporter.to_csv(bs_pairs, f"results_{timestamp}.csv")
        ResultExporter.to_json(bs_pairs, f"results_{timestamp}.json")
        print(f"\nExported → results_{timestamp}.csv / .json")

        # ── Expand gaps using zone data ────────────────────────────────
        expanded_bs_pairs = ResultExpander().expand(
            pairs=bs_pairs,
            provider_zones=provider_zones,
            period_start=PERIOD_START.date(),
            period_end=PERIOD_END.date(),
            durations=_DEFAULT_EXTRACTION_DURATIONS,
            pickup_hour=PICKUP_HOUR,
        )
        ResultExporter.to_csv(expanded_bs_pairs, f"results_expanded_{timestamp}.csv")
        print(f"Exported → results_expanded_{timestamp}.csv")

        # ── Export seasons ─────────────────────────────────────────────
        SeasonExporter.to_csv(provider_zones, f"seasons_{timestamp}.csv")
        SeasonExporter.to_json(provider_zones, f"seasons_{timestamp}.json")
        SeasonExporter.to_csv_unified(provider_zones, f"seasons_unified_{timestamp}.csv")
        SeasonExporter.to_json_unified(provider_zones, f"seasons_unified_{timestamp}.json")
        print(f"Exported → seasons_{timestamp}.csv / .json / _unified.csv / _unified.json")

        # ── Gap report ─────────────────────────────────────────────────
        n_gaps = GapExporter.to_csv(bs_pairs, f"gaps_{timestamp}.csv")
        GapExporter.to_json(bs_pairs, f"gaps_{timestamp}.json")
        if n_gaps:
            print(f"\nWARNING: {n_gaps} gap(s) detected → gaps_{timestamp}.csv")

        # ── Spot check: 50% real + 50% synthetic per provider ─────────
        synthetic_bs = expanded_bs_pairs[len(bs_pairs):]
        half = SPOT_CHECK_COUNT // 2

        for provider, _, _, rate_name in container.providers:
            rate_filter = RateFilter(rate_names=[rate_name])

            real_sample = random.sample(
                [(req, res) for req, res in pairs if res and res.provider.name == provider.name],
                min(half, sum(1 for req, res in pairs if res and res.provider.name == provider.name)),
            )
            # Wrap synthetic domain pairs back into SearchRequest for SpotCheckService
            synthetic_pool = [
                (SearchRequest(search=search, rate_filter=rate_filter), res)
                for search, res in synthetic_bs
                if res and res.provider.name == provider.name
            ]
            synthetic_sample = random.sample(synthetic_pool, min(half, len(synthetic_pool)))

            checker = SpotCheckService(
                factory=container.factory,
                sample_size=len(real_sample) + len(synthetic_sample),
                rate_filter=rate_filter,
            )
            await checker.run(real_sample + synthetic_sample)

    except Exception as e:
        print(f"Critical system failure: {e}")
        raise
    finally:
        end_time = datetime.now()
        elapsed = end_time - start_time
        total_seconds = int(elapsed.total_seconds())
        print(f"\n--- Search Finished ---")
        print(f"End     : {end_time.strftime('%d/%m/%Y %H:%M:%S')}")
        print(f"Duration: {total_seconds // 3600:02d}h {(total_seconds % 3600) // 60:02d}m {total_seconds % 60:02d}s")


if __name__ == "__main__":
    asyncio.run(main())
