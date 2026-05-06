"""Public Polymarket prediction-market context for XAUEX signal decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from math import log10
from typing import Any

import httpx

from xauex.signal.assets import AssetProfile
from xauex.signal.config import SignalConfig

logger = logging.getLogger(__name__)

_SOURCE = "polymarket_gamma+clob"
_SURFACEABLE_MARKET_TYPES = {
    "gold_high",
    "gold_low",
    "gold_up",
    "gold_down",
    "fed_cut",
    "fed_hike",
    "fed_hold",
}


@dataclass(frozen=True)
class _CandidateMarket:
    event_title: str
    event_slug: str
    question: str
    token_id: str
    outcome: str
    market_type: str
    volume: float | None
    liquidity: float | None


def fetch_polymarket_snapshot(*, config: SignalConfig, asset: AssetProfile) -> dict[str, Any]:
    if not bool(getattr(config, "polymarket_context_enabled", False)):
        return {
            "status": "disabled",
            "available": False,
            "source": _SOURCE,
            "summary": "Polymarket prediction-market context is disabled.",
        }
    if asset.symbol != "XAUUSD":
        return {
            "status": "unsupported",
            "available": False,
            "source": _SOURCE,
            "summary": "Polymarket prediction-market context is only evaluated for XAUUSD.",
        }

    fetched_at = datetime.now(timezone.utc)
    search_payloads: list[dict[str, Any]] = []
    token_books: dict[str, dict[str, float | None]] = {}
    errors: list[str] = []
    headers = {
        "User-Agent": _polymarket_user_agent(str(getattr(config, "source_user_agent", "") or "")),
        "Accept": "application/json",
    }
    try:
        with httpx.Client(
            timeout=float(getattr(config, "source_timeout_seconds", 20.0) or 20.0),
            headers=headers,
            follow_redirects=True,
        ) as client:
            for query in getattr(config, "polymarket_search_queries", ("gold", "Fed decision")):
                try:
                    response = client.get(
                        f"{config.polymarket_gamma_base_url}/public-search",
                        params={
                            "q": query,
                            "events_status": "active",
                            "limit_per_type": max(5, int(getattr(config, "polymarket_max_markets", 8) or 8)),
                        },
                    )
                    response.raise_for_status()
                    search_payloads.append(response.json())
                except Exception as exc:  # pragma: no cover - network/provider dependent
                    errors.append(f"search:{query}: {exc}")
                    logger.warning("[POLYMARKET] Search failed for %s: %s", query, exc)

            candidates = _extract_candidate_markets(
                search_payloads,
                max_markets=int(getattr(config, "polymarket_max_markets", 8) or 8),
            )
            for candidate in candidates:
                try:
                    token_books[candidate.token_id] = _fetch_token_book(
                        client=client,
                        clob_base_url=str(getattr(config, "polymarket_clob_base_url", "https://clob.polymarket.com")),
                        token_id=candidate.token_id,
                    )
                except Exception as exc:  # pragma: no cover - network/provider dependent
                    errors.append(f"book:{candidate.token_id}: {exc}")
                    logger.warning("[POLYMARKET] Book fetch failed for %s: %s", candidate.token_id, exc)
    except Exception as exc:  # pragma: no cover - network/provider dependent
        errors.append(f"client: {exc}")
        logger.warning("[POLYMARKET] Snapshot fetch failed: %s", exc)

    if not search_payloads:
        return {
            "status": "unavailable",
            "available": False,
            "source": _SOURCE,
            "errors": errors[:5],
            "summary": "Polymarket prediction-market context is unavailable.",
        }

    snapshot = _normalize_polymarket_payloads(
        search_payloads=search_payloads,
        token_books=token_books,
        fetched_at=fetched_at,
        weight=float(getattr(config, "polymarket_weight", 0.25) or 0.25),
        max_markets=int(getattr(config, "polymarket_max_markets", 8) or 8),
    )
    if errors:
        snapshot["errors"] = errors[:5]
        if snapshot.get("available"):
            snapshot["status"] = "warning"
            snapshot["summary"] = f"{snapshot.get('summary', '')} Partial Polymarket fetch errors: {len(errors)}.".strip()
    return snapshot


def _normalize_polymarket_payloads(
    *,
    search_payloads: list[dict[str, Any]],
    token_books: dict[str, dict[str, Any]],
    fetched_at: datetime,
    weight: float,
    max_markets: int,
) -> dict[str, Any]:
    candidates = _extract_candidate_markets(search_payloads, max_markets=max_markets)
    markets: list[dict[str, Any]] = []
    score_numerator = 0.0
    score_denominator = 0.0

    for candidate in candidates:
        book = token_books.get(candidate.token_id) or {}
        midpoint = _parse_float(book.get("midpoint"))
        if midpoint is None:
            continue
        contribution = _market_contribution(candidate.market_type, midpoint)
        money_weight = _money_weight(candidate.volume, candidate.liquidity)
        if contribution != 0.0 and money_weight > 0:
            score_numerator += contribution * money_weight
            score_denominator += money_weight
        best_bid = _parse_float(book.get("best_bid"))
        best_ask = _parse_float(book.get("best_ask"))
        spread = round(best_ask - best_bid, 4) if best_bid is not None and best_ask is not None else None
        markets.append(
            {
                "event_title": candidate.event_title,
                "event_slug": candidate.event_slug,
                "question": candidate.question,
                "market_type": candidate.market_type,
                "outcome": candidate.outcome,
                "outcome_token_id": candidate.token_id,
                "yes_midpoint": round(midpoint, 4),
                "best_bid": round(best_bid, 4) if best_bid is not None else None,
                "best_ask": round(best_ask, 4) if best_ask is not None else None,
                "spread": spread,
                "volume": round(candidate.volume, 4) if candidate.volume is not None else None,
                "liquidity": round(candidate.liquidity, 4) if candidate.liquidity is not None else None,
                "money_weight": round(money_weight, 4),
                "contribution": round(contribution, 4),
                "bias": _bias_from_score(contribution),
            }
        )

    if not markets:
        return {
            "status": "unavailable",
            "available": False,
            "source": _SOURCE,
            "fetched_at_utc": fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "weight": round(max(0.0, min(0.40, weight)), 4),
            "market_count": 0,
            "markets": [],
            "overall_bias": "NEUTRAL",
            "money_weighted_score": 0.0,
            "summary": "Polymarket prediction-market context is unavailable; no active supported market prices were found.",
        }

    score = score_numerator / score_denominator if score_denominator > 0 else 0.0
    bias = _bias_from_score(score)
    bounded_weight = round(max(0.0, min(0.40, weight)), 4)
    return {
        "status": "available",
        "available": True,
        "source": _SOURCE,
        "fetched_at_utc": fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "weight": bounded_weight,
        "market_count": len(markets),
        "markets": markets,
        "overall_bias": bias,
        "money_weighted_score": round(score, 4),
        "summary": (
            f"Polymarket money-weighted prediction-market bias is {bias} "
            f"(score={score:.4f}, weight={bounded_weight:.0%}, markets={len(markets)})."
        ),
    }


def _extract_candidate_markets(search_payloads: list[dict[str, Any]], *, max_markets: int) -> list[_CandidateMarket]:
    candidates: list[_CandidateMarket] = []
    seen_tokens: set[str] = set()
    for payload in search_payloads:
        for event in payload.get("events") or []:
            event_title = str(event.get("title") or "")
            event_slug = str(event.get("slug") or "")
            for market in event.get("markets") or []:
                if not _is_active_open_market(market):
                    continue
                token_ids = _parse_json_list(market.get("clobTokenIds"))
                outcomes = _parse_json_list(market.get("outcomes")) or ["Yes", "No"]
                token_id, outcome = _select_yes_token(token_ids, outcomes)
                if not token_id or token_id in seen_tokens:
                    continue
                question = str(market.get("question") or "")
                market_type = _market_type(question, outcome)
                if market_type not in _SURFACEABLE_MARKET_TYPES:
                    continue
                seen_tokens.add(token_id)
                candidates.append(
                    _CandidateMarket(
                        event_title=event_title,
                        event_slug=event_slug,
                        question=question,
                        token_id=token_id,
                        outcome=outcome,
                        market_type=market_type,
                        volume=_parse_float(market.get("volumeNum") if market.get("volumeNum") is not None else market.get("volume")),
                        liquidity=_parse_float(market.get("liquidityNum") if market.get("liquidityNum") is not None else market.get("liquidity")),
                    )
                )
    candidates.sort(key=lambda item: (_market_priority(item), -_raw_money(item.volume, item.liquidity), item.question))
    return candidates[: max(1, max_markets)]


def _fetch_token_book(*, client: httpx.Client, clob_base_url: str, token_id: str) -> dict[str, float | None]:
    midpoint_response = client.get(f"{clob_base_url.rstrip('/')}/midpoint", params={"token_id": token_id})
    midpoint_response.raise_for_status()
    midpoint = _parse_float((midpoint_response.json() or {}).get("mid"))

    book_response = client.get(f"{clob_base_url.rstrip('/')}/book", params={"token_id": token_id})
    book_response.raise_for_status()
    book = book_response.json() or {}
    bids = [_parse_float(row.get("price")) for row in book.get("bids") or [] if isinstance(row, dict)]
    asks = [_parse_float(row.get("price")) for row in book.get("asks") or [] if isinstance(row, dict)]
    bids = [value for value in bids if value is not None]
    asks = [value for value in asks if value is not None]
    return {
        "midpoint": midpoint,
        "best_bid": max(bids) if bids else None,
        "best_ask": min(asks) if asks else None,
    }


def _polymarket_user_agent(source_user_agent: str) -> str:
    if "mozilla" in source_user_agent.lower():
        return source_user_agent
    suffix = source_user_agent or "XAUEX-Signal/2.0"
    return f"Mozilla/5.0 (compatible; {suffix})"


def _is_active_open_market(market: dict[str, Any]) -> bool:
    if _as_bool(market.get("closed")):
        return False
    active = market.get("active")
    return True if active is None else _as_bool(active)


def _select_yes_token(token_ids: list[str], outcomes: list[str]) -> tuple[str, str]:
    if not token_ids:
        return "", ""
    for idx, outcome in enumerate(outcomes):
        if str(outcome).strip().lower() in {"yes", "up"} and idx < len(token_ids):
            return token_ids[idx], str(outcome)
    return token_ids[0], str(outcomes[0]) if outcomes else "Yes"


def _market_type(question: str, outcome: str) -> str:
    text = question.lower()
    outcome_text = outcome.lower()
    if "gold" in text or "xau" in text or "(gc)" in text:
        if "(high)" in text or " hit high" in text:
            return "gold_high"
        if "(low)" in text or " hit low" in text:
            return "gold_low"
        if "up or down" in text:
            if outcome_text == "up":
                return "gold_up"
            if outcome_text == "down":
                return "gold_down"
    if "fed" in text and ("interest rate" in text or "fed decision" in text or "rates" in text):
        if "decrease" in text or "cut" in text:
            return "fed_cut"
        if "increase" in text or "hike" in text:
            return "fed_hike"
        if "no change" in text or "unchanged" in text or "pause" in text:
            return "fed_hold"
    return "other"


def _market_contribution(market_type: str, midpoint: float) -> float:
    probability_edge = max(0.0, midpoint - 0.5)
    if market_type in {"gold_high", "gold_up", "fed_cut"}:
        return probability_edge
    if market_type in {"gold_low", "gold_down", "fed_hike"}:
        return -probability_edge
    return 0.0


def _bias_from_score(score: float) -> str:
    if score >= 0.03:
        return "BUY"
    if score <= -0.03:
        return "SELL"
    return "NEUTRAL"


def _money_weight(volume: float | None, liquidity: float | None) -> float:
    money = _raw_money(volume, liquidity)
    if money <= 0:
        return 1.0
    return max(1.0, log10(money + 10.0))


def _raw_money(volume: float | None, liquidity: float | None) -> float:
    return max(0.0, float(volume or 0.0)) + max(0.0, float(liquidity or 0.0))


def _market_priority(candidate: _CandidateMarket) -> int:
    if candidate.market_type in {"gold_high", "gold_low", "gold_up", "gold_down"}:
        text = candidate.question.lower()
        if "xau" in text:
            return 0
        return 1
    if candidate.market_type in {"fed_cut", "fed_hike"}:
        return 2
    if candidate.market_type == "fed_hold":
        return 3
    return 3


def _parse_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
