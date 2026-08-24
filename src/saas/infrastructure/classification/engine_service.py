"""AcrissEngineClassificationService — engine v2 behind the existing seam.

Implements ClassificationService with the deterministic ACRISS engine plus the
project-specific layers the spec does not know about:

  1. BUNDLES. A provider group's example_models often lists several models.
     Every member is classified individually; if they resolve to different
     codes the group is priced to the MOST EXPENSIVE member (unchanged
     business rule from engine v1) with pending_review=True and capped
     confidence.
  2. PROVIDER-DECLARED CODES. Many providers expose ACRISS-shaped group codes
     (e.g. recordgo's MBMR). They fill missing letters: transmission (often
     not scraped) with high trust; category/type only when the engine has
     nothing.
  3. SEMANTIC LLM FALLBACK (§31). Unknown models go to SemanticModelResolver,
     which returns a profile — never a code. Its suggestions land in the
     acriss_review_queue (§32) and are NEVER auto-promoted (§33).
  4. MATERIALIZED-CATALOG GUARD. A code the engine builds but the catalog does
     not materialize is persisted as NULL + pending_review (the FK demands it);
     the proposed code survives in classification_detail and the review queue.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from src.saas.application.classification.acriss_engine import (
    VehicleInput,
    classify_vehicle,
    load_dictionary,
    load_source_overrides,
)
from src.saas.application.classification.acriss_engine.codes import (
    CATEGORY_NAMES,
    FUEL_NAMES,
    TRANSMISSION_NAMES,
    TYPE_NAMES,
)
from src.saas.application.classification.acriss_engine.normalizer import (
    norm_key,
    normalize_for_matching,
)
from src.saas.application.classification.acriss_engine.types import (
    AcrissResult,
    LetterResult,
)
from src.saas.application.classification.acriss_loader import load_acriss_specs
from src.saas.application.classification.dtos import (
    ClassificationResult,
    VehicleClassificationInput,
)
from src.saas.application.classification.service import ClassificationService

logger = logging.getLogger(__name__)

_YAML_PATH = Path(__file__).parents[4] / "acriss_codes.yaml"

# Price ladder for the most-expensive-member rule (catalog convention:
# M·N·E·H·C·D·I·J·S·R·F·G·P·U·L·W·X).
_CATEGORY_SCALE = "MNEHCDIJSRFGPULWX"

_BUNDLE_SEPARATORS = (",", ";", " / ", " | ")


def _split_bundle(example_models: str) -> list[str]:
    text = example_models or ""
    for sep in _BUNDLE_SEPARATORS[1:]:
        text = text.replace(sep, ",")
    members = [m.strip() for m in text.split(",") if m.strip()]
    # dedupe, keep order
    seen: set[str] = set()
    out = []
    for m in members:
        key = norm_key(m)
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def _category_rank(code: Optional[str]) -> int:
    if not code or code not in _CATEGORY_SCALE:
        return -1
    return _CATEGORY_SCALE.index(code)


def _count_by(dtos, attr: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in dtos:
        key = getattr(d, attr) or "?"
        out[key] = out.get(key, 0) + 1
    return out


def _parse_declared_code(*candidates: Optional[str]) -> Optional[str]:
    """Return a structurally valid 4-char ACRISS code among the candidates."""
    for cand in candidates:
        if not cand:
            continue
        code = cand.strip().upper()
        if (
            len(code) == 4
            and code[0] in CATEGORY_NAMES
            and code[1] in TYPE_NAMES
            and code[2] in TRANSMISSION_NAMES
            and code[3] in FUEL_NAMES
        ):
            return code
    return None


class AcrissEngineClassificationService(ClassificationService):
    def __init__(
        self,
        materialized_codes: Optional[set[str]] = None,
        resolver=None,                       # SemanticModelResolver | None
        session_factory: Optional[Callable] = None,  # context-manager factory for queue writes
        dictionary=None,
        overrides: Optional[dict] = None,
    ) -> None:
        if materialized_codes is None:
            materialized_codes = {s.code for s in load_acriss_specs(_YAML_PATH)}
        self._codes = materialized_codes
        self._resolver = resolver
        self._session_factory = session_factory
        self._dictionary = dictionary if dictionary is not None else load_dictionary()
        self._overrides = overrides if overrides is not None else load_source_overrides()

    # ── ClassificationService ────────────────────────────────────────────────

    def classify_provider_batch(
        self,
        provider_code: str,
        vehicles: list[VehicleClassificationInput],
    ) -> list[ClassificationResult]:
        chosen_results: list[AcrissResult] = []
        bundle_notes: list[list[str]] = []
        unresolved_members: list[AcrissResult] = []

        for v in vehicles:
            members = _split_bundle(v.example_models) or [
                v.external_name or v.external_code or ""
            ]
            member_results = [
                classify_vehicle(
                    VehicleInput(
                        raw_model=m,
                        transmission=v.transmission,
                        seats=v.seats,
                        source=provider_code,
                    ),
                    self._dictionary,
                    self._overrides,
                )
                for m in members
            ]
            chosen, notes, unresolved = self._combine_bundle(member_results)
            chosen = self._apply_declared_code(chosen, v, notes)
            chosen_results.append(chosen)
            bundle_notes.append(notes)
            unresolved_members.extend(u for u in unresolved if u is not chosen)

        self._resolve_unknowns(provider_code, vehicles, chosen_results, bundle_notes)
        self._queue_unresolved(
            provider_code, chosen_results + unresolved_members
        )

        dtos = [
            self._to_dto(r, notes) for r, notes in zip(chosen_results, bundle_notes)
        ]
        self._record_metrics(provider_code, chosen_results, bundle_notes, dtos)
        return dtos

    # ── Bundle rule ──────────────────────────────────────────────────────────

    def _combine_bundle(
        self, members: list[AcrissResult]
    ) -> tuple[AcrissResult, list[str], list[AcrissResult]]:
        """Returns (chosen, notes, unresolved_members).

        Unresolved members don't poison the bundle: when at least one member
        resolves, the group is decided among the RESOLVED ones and the unknown
        siblings are surfaced separately (review queue) instead of dragging
        the whole group to mixed/0.65."""
        if len(members) == 1:
            m = members[0]
            return m, [], [m] if m.model_unresolved else []

        unresolved = [m for m in members if m.model_unresolved]
        pool = [m for m in members if not m.model_unresolved] or members
        notes: list[str] = []
        if unresolved and pool is not members:
            notes.append(
                f"{len(unresolved)} bundle member(s) unresolved, "
                "excluded from the group decision: "
                + ", ".join(m.raw_model for m in unresolved)
            )

        codes = {m.partial_acriss for m in pool}
        if len(codes) == 1:
            best = max(pool, key=lambda m: m.confidence)
            if unresolved and not best.model_unresolved:
                best.assumptions = best.assumptions + notes
            return best, notes + [
                f"bundle of {len(members)} models, resolved members classify identically"
            ], unresolved

        # Mixed bundle: price to the MOST EXPENSIVE member (business rule).
        best = max(
            pool,
            key=lambda m: (_category_rank(m.category.code), m.confidence),
        )
        summary = "; ".join(f"{m.raw_model} -> {m.partial_acriss}" for m in pool)
        best.needs_review = True
        best.confidence = min(best.confidence, 0.65)
        best.assumptions = best.assumptions + [
            f"Mixed bundle priced to the most expensive member: {summary}"
        ]
        return best, notes + [f"mixed bundle: {summary}"], unresolved

    # ── Provider-declared ACRISS-shaped group codes ──────────────────────────

    def _apply_declared_code(
        self, r: AcrissResult, v: VehicleClassificationInput, notes: list[str]
    ) -> AcrissResult:
        declared = _parse_declared_code(v.external_code, v.external_name)
        if not declared:
            return r

        if r.transmission.code is None:
            r.transmission = LetterResult(
                code=declared[2],
                name=TRANSMISSION_NAMES.get(declared[2]),
                confidence=0.90,
                source="source_override",
                explanation=f"{declared[2]} from provider-declared group code {declared}",
            )
            notes.append(f"transmission taken from declared code {declared}")

        if r.fuel_power.code is None or r.fuel_power.confidence < 0.85:
            if declared[3] != r.fuel_power.code:
                notes.append(
                    f"fuel {r.fuel_power.code}->{declared[3]} from declared code {declared}"
                )
            r.fuel_power = LetterResult(
                code=declared[3],
                name=FUEL_NAMES.get(declared[3]),
                confidence=0.85,
                source="source_override",
                explanation=f"{declared[3]} from provider-declared group code {declared}",
            )

        if r.category.code is None:
            r.category = LetterResult(
                code=declared[0],
                name=CATEGORY_NAMES.get(declared[0]),
                confidence=0.75,
                source="source_override",
                explanation=f"{declared[0]} from provider-declared group code {declared}",
            )
        if r.type.code is None:
            r.type = LetterResult(
                code=declared[1],
                name=TYPE_NAMES.get(declared[1]),
                confidence=0.75,
                source="source_override",
                explanation=f"{declared[1]} from provider-declared group code {declared}",
            )

        self._refresh_aggregate(r)
        return r

    # ── Semantic LLM fallback for unknown models (§31) ───────────────────────

    def _resolve_unknowns(
        self,
        provider_code: str,
        vehicles: list[VehicleClassificationInput],
        results: list[AcrissResult],
        bundle_notes: list[list[str]],
    ) -> None:
        idx_unknown = [
            i for i, r in enumerate(results)
            if r.model_unresolved and (r.category.code is None or r.type.code is None)
        ]
        if not idx_unknown or self._resolver is None:
            return

        names = [results[i].raw_model for i in idx_unknown]
        profiles = self._resolver.resolve(names)
        for i, profile in zip(idx_unknown, profiles):
            if not profile:
                continue
            r = results[i]
            conf = min(float(profile.get("confidence", 0.5)), 0.84)
            cat = profile.get("likely_category")
            typ = profile.get("likely_type")
            if r.category.code is None and cat in CATEGORY_NAMES:
                r.category = LetterResult(
                    code=cat, name=CATEGORY_NAMES[cat], confidence=conf,
                    source="heuristic",
                    explanation=f"{cat} from semantic resolver: {profile.get('reason', '')}",
                )
            if r.type.code is None and typ in TYPE_NAMES:
                r.type = LetterResult(
                    code=typ, name=TYPE_NAMES[typ], confidence=conf,
                    source="heuristic",
                    explanation=f"{typ} from semantic resolver: {profile.get('reason', '')}",
                )
            mode = profile.get("powertrain_profile")
            if r.fuel_power.source == "fallback" and mode == "bev_only":
                r.fuel_power = LetterResult(
                    code="E", name=FUEL_NAMES["E"], confidence=conf,
                    source="heuristic", explanation="E from semantic resolver (bev_only)",
                )
            elif r.fuel_power.source == "fallback" and mode in ("hybrid_only",):
                r.fuel_power = LetterResult(
                    code="H", name=FUEL_NAMES["H"], confidence=conf,
                    source="heuristic", explanation="H from semantic resolver (hybrid_only)",
                )
            r.assumptions = r.assumptions + [
                f"Unknown model resolved semantically (LLM, conf {conf:.2f})"
            ]
            bundle_notes[i].append("semantic resolver used")
            self._refresh_aggregate(r)

    # ── Review queue (§32) ───────────────────────────────────────────────────

    def _queue_unresolved(
        self,
        provider_code: str,
        results: list[AcrissResult],
    ) -> None:
        if self._session_factory is None:
            return
        pending = [r for r in results if r.model_unresolved and r.raw_model.strip()]
        if not pending:
            return
        try:
            from src.saas.infrastructure.persistence.repositories import (
                AcrissReviewQueueRepository,
            )
            with self._session_factory() as session:
                repo = AcrissReviewQueueRepository(session)
                for r in pending:
                    repo.upsert_sighting(
                        normalized_model=normalize_for_matching(r.raw_model),
                        raw_model=r.raw_model,
                        source=provider_code,
                        suggested_category=r.category.code,
                        suggested_type=r.type.code,
                        suggested_powertrain=None,
                        suggested_acriss=r.acriss,
                        confidence=round(r.confidence, 3),
                        reason=r.explanation.get("category") or None,
                    )
                session.commit()
        except Exception as exc:  # noqa: BLE001 — queueing must never break a scrape
            logger.warning("acriss_review_queue write failed: %s", exc)

    # ── Metrics (§38) ────────────────────────────────────────────────────────

    def _record_metrics(self, provider_code, results, bundle_notes, dtos) -> None:
        """Aggregate per-batch counters (§38): logged as one summary line and
        kept on `last_batch_metrics` for callers/tests to inspect."""
        letter_sources = [
            lr.source for r in results
            for lr in (r.category, r.type, r.transmission, r.fuel_power)
        ]
        metrics = {
            "provider": provider_code,
            "total_classified": len(results),
            "high_confidence": sum(1 for d in dtos if d.confidence >= 0.85),
            "needs_review": sum(1 for d in dtos if d.pending_review),
            "unknown_models": sum(1 for r in results if r.model_unresolved),
            "source_overrides_used": sum(
                1 for s in letter_sources if s == "source_override"
            ),
            "heuristic_letters": sum(
                1 for s in letter_sources if s in ("heuristic", "fallback")
            ),
            "powertrain_ambiguous": sum(1 for r in results if r.powertrain_ambiguous),
            "passenger_van": sum(1 for r in results if r.type.code == "V"),
            "mixed_bundles": sum(
                1 for notes in bundle_notes
                if any(n.startswith("mixed bundle") for n in notes)
            ),
            "unmaterialized": sum(
                1 for d in dtos
                if d.detail and "unmaterialized_code" in d.detail
            ),
            "confidence_distribution": {
                band: sum(1 for d in dtos if lo <= d.confidence < hi)
                for band, lo, hi in (
                    ("<0.65", 0.0, 0.65), ("0.65-0.85", 0.65, 0.85),
                    ("0.85-0.95", 0.85, 0.95), (">=0.95", 0.95, 1.01),
                )
            },
            "by_category": _count_by(dtos, "acriss_category"),
            "by_type": _count_by(dtos, "acriss_body_type"),
        }
        self.last_batch_metrics = metrics
        logger.info("[acriss-engine] %s", metrics)

    # ── Output mapping ───────────────────────────────────────────────────────

    def _refresh_aggregate(self, r: AcrissResult) -> None:
        """Recompute aggregates AND needs_review from scratch: a letter that a
        later stage repaired (declared code, semantic resolver) must be able
        to clear a review flag the engine set for the unrepaired state. The
        mixed-bundle flag is reapplied authoritatively in _to_dto."""
        letters = [r.category, r.type, r.transmission, r.fuel_power]
        r.partial_acriss = "".join(l.code if l.code else "?" for l in letters)
        r.acriss = r.partial_acriss if "?" not in r.partial_acriss else None
        r.confidence = min(l.confidence for l in letters)
        r.needs_review = (
            r.confidence < 0.85
            or r.category.confidence < 0.85
            or r.type.confidence < 0.85
            or r.model_unresolved
            or (r.powertrain_ambiguous and r.fuel_power.confidence < 0.85)
        )

    def _to_dto(self, r: AcrissResult, notes: list[str]) -> ClassificationResult:
        # The mixed-bundle cap is authoritative here — letter-level refreshes
        # (declared codes, semantic resolver) must not raise it back up.
        if any(n.startswith("mixed bundle") for n in notes):
            r.confidence = min(r.confidence, 0.65)
            r.needs_review = True

        detail = r.to_dict()
        if notes:
            detail["service_notes"] = notes

        materialized = bool(r.acriss and r.acriss in self._codes)
        if r.acriss and not materialized:
            detail["unmaterialized_code"] = r.acriss

        if r.acriss and materialized:
            return ClassificationResult(
                acriss_category=r.category.code,
                acriss_body_type=r.type.code,
                acriss_transmission=r.transmission.code,
                acriss_fuel=r.fuel_power.code,
                confidence=r.confidence,
                pending_review=r.needs_review,
                rationale="; ".join(
                    v for v in r.explanation.values() if v
                ) or None,
                detail=detail,
            )

        # No code, or a code the catalog does not materialize: NULL attributes
        # (the PVC FK demands it); everything survives in detail.
        rationale = (
            f"engine code {r.acriss} not materialized in acriss_codes catalog"
            if r.acriss
            else f"unresolved: partial {r.partial_acriss}"
        )
        return ClassificationResult(
            acriss_category=None,
            acriss_body_type=None,
            acriss_transmission=None,
            acriss_fuel=None,
            confidence=r.confidence,
            pending_review=True,
            rationale=rationale,
            detail=detail,
        )
