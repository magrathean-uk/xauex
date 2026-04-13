"""Build a normalized text dossier from curated sources."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import html as html_lib
import json
import logging
import re
from typing import Optional

import httpx

try:
    import feedparser  # type: ignore
except Exception:  # pragma: no cover
    feedparser = None

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None

from xauex.signal.assets import AssetProfile
from xauex.signal.config import SignalConfig
from xauex.signal.source_registry import SourceDefinition, get_sources

logger = logging.getLogger(__name__)

_MAX_HTML_CHARS = 1400
_MAX_SUMMARY_CHARS = 900
_WHITESPACE_RE = re.compile(r'\s+')
_TAG_RE = re.compile(r'<[^>]+>')


@dataclass(frozen=True)
class ContextItem:
    source_id: str
    source_name: str
    provider: str
    category: str
    tier: int
    official: bool
    title: str
    url: str
    published_utc: str
    summary: str
    freshness_hours: float
    freshness_score: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ContextBundle:
    asset: str
    generated_at_utc: str
    source_count: int
    item_count: int
    markdown: str
    items: list[ContextItem]

    def to_dict(self) -> dict:
        return {
            'asset': self.asset,
            'generated_at_utc': self.generated_at_utc,
            'source_count': self.source_count,
            'item_count': self.item_count,
            'markdown': self.markdown,
            'items': [item.to_dict() for item in self.items],
        }


class ContextBuilder:
    def __init__(self, config: SignalConfig):
        self.config = config
        self.client = httpx.Client(
            timeout=config.source_timeout_seconds,
            headers={
                'User-Agent': config.source_user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,text/xml;q=0.8,*/*;q=0.7',
            },
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def build(
        self,
        asset: AssetProfile,
        *,
        lookback_hours: int = 72,
        max_sources: int = 10,
        max_items_per_source: int = 4,
        auto_fetch_only: bool = True,
    ) -> ContextBundle:
        source_rows = get_sources(asset.symbol, auto_fetch_only=auto_fetch_only)
        if max_sources > 0:
            source_rows = source_rows[:max_sources]

        items: list[ContextItem] = []
        for row in source_rows:
            try:
                if row.kind == 'rss':
                    items.extend(self._fetch_rss(row, lookback_hours=lookback_hours, item_limit=max_items_per_source))
                elif row.kind in {'html', 'api'}:
                    item = self._fetch_page(row)
                    if item is not None:
                        items.append(item)
                else:
                    logger.debug('[CONTEXT] Unsupported source kind=%s id=%s', row.kind, row.id)
            except Exception as exc:  # pragma: no cover - network dependent
                logger.warning('[CONTEXT] Source %s failed: %s', row.id, exc)

        items = self._dedupe_and_rank_items(items)
        if not items:
            raise RuntimeError(
                'No context items could be fetched. Either provide --news/--news-text or disable auto_fetch_only and add manual adapters.'
            )

        generated_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        markdown = self._render_markdown(asset, items, generated_at)
        return ContextBundle(
            asset=asset.symbol,
            generated_at_utc=generated_at,
            source_count=len(source_rows),
            item_count=len(items),
            markdown=markdown,
            items=items,
        )

    def _fetch_rss(self, row: SourceDefinition, *, lookback_hours: int, item_limit: int) -> list[ContextItem]:
        if feedparser is None:
            raise RuntimeError('feedparser is not installed; add it from xauex/requirements.txt')
        response = self.client.get(row.url)
        response.raise_for_status()
        parsed = feedparser.parse(response.text)
        entries = getattr(parsed, 'entries', []) or []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        out: list[ContextItem] = []
        for entry in entries:
            published = self._entry_timestamp(entry) or datetime.now(timezone.utc)
            if published < cutoff and out:
                continue
            title = self._clean(getattr(entry, 'title', '') or '(untitled)')
            link = getattr(entry, 'link', '') or row.homepage
            raw_summary = getattr(entry, 'summary', '') or getattr(entry, 'description', '') or title
            summary = self._clean(raw_summary, max_chars=_MAX_SUMMARY_CHARS)
            out.append(
                ContextItem(
                    source_id=row.id,
                    source_name=row.name,
                    provider=row.provider,
                    category=row.category,
                    tier=row.tier,
                    official=row.official,
                    title=title,
                    url=link,
                    published_utc=published.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    summary=summary,
                    freshness_hours=round(max(0.0, (datetime.now(timezone.utc) - published).total_seconds() / 3600.0), 2),
                    freshness_score=round(self._freshness_score(published), 4),
                )
            )
            if len(out) >= item_limit:
                break
        return out

    def _fetch_page(self, row: SourceDefinition) -> Optional[ContextItem]:
        response = self.client.get(row.url)
        response.raise_for_status()
        text = response.text
        title = row.name
        summary_parts: list[str] = []
        if BeautifulSoup is not None:
            soup = BeautifulSoup(text, 'html.parser')
            if soup.title and soup.title.text:
                title = self._clean(soup.title.text, max_chars=140)
            meta = soup.find('meta', attrs={'name': 'description'})
            if meta and meta.get('content'):
                summary_parts.append(self._clean(meta['content']))
            og = soup.find('meta', attrs={'property': 'og:description'})
            if og and og.get('content'):
                summary_parts.append(self._clean(og['content']))
            for tag_name in ('h1', 'h2', 'p'):
                for tag in soup.find_all(tag_name, limit=6):
                    text_value = self._clean(tag.get_text(' ', strip=True))
                    if text_value and text_value not in summary_parts:
                        summary_parts.append(text_value)
                    joined = ' '.join(summary_parts)
                    if len(joined) >= _MAX_HTML_CHARS:
                        break
                if len(' '.join(summary_parts)) >= _MAX_HTML_CHARS:
                    break
        else:  # pragma: no cover
            summary_parts.append(self._clean(text, max_chars=_MAX_HTML_CHARS))
        summary = self._clean(' '.join(summary_parts), max_chars=_MAX_HTML_CHARS)
        if not summary:
            return None
        return ContextItem(
            source_id=row.id,
            source_name=row.name,
            provider=row.provider,
            category=row.category,
            tier=row.tier,
            official=row.official,
            title=title,
            url=row.url,
            published_utc=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            summary=summary,
            freshness_hours=0.0,
            freshness_score=1.0,
        )

    def _render_markdown(self, asset: AssetProfile, items: list[ContextItem], generated_at: str) -> str:
        lines: list[str] = []
        lines.append(f'# Auto-built market context for {asset.symbol}')
        lines.append('')
        lines.append(f'Generated at UTC: {generated_at}')
        lines.append(f'Asset: {asset.display_name}')
        lines.append(f'Asset class: {asset.asset_class}')
        lines.append('')
        lines.append('## Simulation brief')
        lines.append(asset.simulation_requirement)
        lines.append('')
        lines.append('## Normalized source items')
        for idx, item in enumerate(items, start=1):
            lines.append(f'### {idx}. {item.title}')
            lines.append(f'- Source: {item.source_name} ({item.provider})')
            lines.append(f'- Category: {item.category}')
            lines.append(f'- Tier: {item.tier}')
            lines.append(f'- Official: {"yes" if item.official else "no"}')
            lines.append(f'- Published UTC: {item.published_utc}')
            lines.append(f'- Freshness score: {item.freshness_score}')
            lines.append(f'- URL: {item.url}')
            lines.append('- Summary:')
            lines.append(item.summary)
            lines.append('')
        lines.append('## Instruction to the swarm')
        lines.append(
            'Use the normalized items above as the market dossier. Weight official releases above commentary, '
            'give more importance to information inside the lookback window, and explain where consensus and disagreement exist.'
        )
        return '\n'.join(lines).strip() + '\n'

    @staticmethod
    def _entry_timestamp(entry) -> Optional[datetime]:
        for attr in ('published_parsed', 'updated_parsed'):
            value = getattr(entry, attr, None)
            if value:
                return datetime(*value[:6], tzinfo=timezone.utc)
        for attr in ('published', 'updated'):
            value = getattr(entry, attr, None)
            if not value:
                continue
            try:
                dt = parsedate_to_datetime(value)
            except Exception:
                try:
                    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
                except Exception:
                    continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        return None

    @staticmethod
    def _clean(value: str, *, max_chars: int = 1200) -> str:
        if not value:
            return ''
        text = html_lib.unescape(value)
        text = _TAG_RE.sub(' ', text)
        text = _WHITESPACE_RE.sub(' ', text).strip()
        if len(text) > max_chars:
            text = text[: max_chars - 3].rstrip() + '...'
        return text

    @staticmethod
    def _freshness_score(published: datetime) -> float:
        age_hours = max(0.0, (datetime.now(timezone.utc) - published).total_seconds() / 3600.0)
        if age_hours <= 6:
            return 1.0
        if age_hours <= 24:
            return 0.85
        if age_hours <= 48:
            return 0.65
        if age_hours <= 72:
            return 0.45
        return 0.25

    @staticmethod
    def _dedupe_and_rank_items(items: list[ContextItem]) -> list[ContextItem]:
        deduped: dict[str, ContextItem] = {}
        for item in items:
            key = ContextBuilder._dedupe_key(item)
            existing = deduped.get(key)
            if existing is None or ContextBuilder._ranking_key(item) < ContextBuilder._ranking_key(existing):
                deduped[key] = item
        return sorted(deduped.values(), key=ContextBuilder._ranking_key)

    @staticmethod
    def _dedupe_key(item: ContextItem) -> str:
        title = _WHITESPACE_RE.sub(' ', item.title.lower()).strip()
        summary = _WHITESPACE_RE.sub(' ', item.summary.lower()).strip()
        return f'{title}|{summary[:160]}'

    @staticmethod
    def _ranking_key(item: ContextItem) -> tuple[float, float, float, str]:
        return (
            float(item.tier),
            -float(item.freshness_score),
            0.0 if item.official else 1.0,
            item.source_name.lower(),
        )
