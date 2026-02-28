from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Literal

from stockotter_v2.schemas import Cluster, NewsItem

_NO_TICKER_KEY = "_NO_TICKER_"
_TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣]+")
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "from",
        "that",
        "this",
        "with",
        "news",
        "기사",
        "관련",
        "대한",
        "했다",
        "한다",
        "에서",
        "으로",
    }
)


@dataclass(frozen=True)
class _VectorizedNews:
    item: NewsItem
    tokens: tuple[str, ...]
    vector: dict[str, float]
    norm: float


class TfidfClusterer:
    def __init__(
        self,
        *,
        similarity_threshold: float = 0.35,
        representative_policy: Literal["earliest", "keyword"] = "earliest",
    ) -> None:
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be between 0.0 and 1.0")
        if representative_policy not in {"earliest", "keyword"}:
            raise ValueError("representative_policy must be one of: earliest, keyword")
        self.similarity_threshold = similarity_threshold
        self.representative_policy = representative_policy

    def cluster(self, items: list[NewsItem]) -> list[Cluster]:
        grouped: dict[str, list[NewsItem]] = defaultdict(list)
        for item in items:
            grouped[self._ticker_key(item)].append(item)

        clusters: list[Cluster] = []
        for ticker in sorted(grouped):
            ordered_items = sorted(grouped[ticker], key=lambda item: (item.published_at, item.id))
            vectorized_items = self._vectorize_items(ordered_items)
            grouped_vectors = self._group_similar_vectors(vectorized_items)
            for members in grouped_vectors:
                clusters.append(self._build_cluster(ticker=ticker, members=members))
        return clusters

    def _group_similar_vectors(
        self, vectorized_items: list[_VectorizedNews]
    ) -> list[list[_VectorizedNews]]:
        clusters: list[list[_VectorizedNews]] = []
        for candidate in vectorized_items:
            best_index = -1
            best_similarity = self.similarity_threshold
            for index, members in enumerate(clusters):
                similarity = max(
                    self._cosine_similarity(candidate, member) for member in members
                )
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_index = index
            if best_index >= 0:
                clusters[best_index].append(candidate)
            else:
                clusters.append([candidate])
        return clusters

    def _build_cluster(self, *, ticker: str, members: list[_VectorizedNews]) -> Cluster:
        ordered_members = sorted(
            members, key=lambda member: (member.item.published_at, member.item.id)
        )
        member_news_ids = [member.item.id for member in ordered_members]
        representative = self._pick_representative(ordered_members)
        return Cluster(
            cluster_id=self._cluster_id(ticker=ticker, member_news_ids=member_news_ids),
            representative_news_id=representative.item.id,
            member_news_ids=member_news_ids,
            summary=self._build_summary(ticker=ticker, members=ordered_members),
        )

    def _pick_representative(self, members: list[_VectorizedNews]) -> _VectorizedNews:
        if self.representative_policy == "keyword":
            ranked = sorted(
                members,
                key=lambda member: (
                    -len(set(member.tokens)),
                    member.item.published_at,
                    member.item.id,
                ),
            )
            return ranked[0]
        return min(members, key=lambda member: (member.item.published_at, member.item.id))

    def _build_summary(self, *, ticker: str, members: list[_VectorizedNews]) -> str:
        token_counts: Counter[str] = Counter()
        for member in members:
            token_counts.update(member.tokens)
        top_keywords = [
            token
            for token, _ in sorted(token_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:3]
        ]

        ticker_label = "NO_TICKER" if ticker == _NO_TICKER_KEY else ticker
        if not top_keywords:
            return f"{ticker_label} 기사 {len(members)}건"
        return f"{ticker_label} 기사 {len(members)}건: {', '.join(top_keywords)}"

    def _cluster_id(self, *, ticker: str, member_news_ids: list[str]) -> str:
        digest_input = f"{ticker}|{'|'.join(sorted(member_news_ids))}"
        digest = hashlib.sha1(digest_input.encode("utf-8")).hexdigest()[:12]
        return f"{ticker}-{digest}"

    @staticmethod
    def _ticker_key(item: NewsItem) -> str:
        if not item.tickers_mentioned:
            return _NO_TICKER_KEY
        return sorted(set(item.tickers_mentioned))[0]

    def _vectorize_items(self, items: list[NewsItem]) -> list[_VectorizedNews]:
        tokenized_docs = [
            tuple(self._tokenize(f"{item.title}\n{item.raw_text}"))
            for item in items
        ]
        doc_freq: Counter[str] = Counter()
        for tokens in tokenized_docs:
            doc_freq.update(set(tokens))

        total_docs = len(items)
        idf = {
            term: math.log((1 + total_docs) / (1 + frequency)) + 1.0
            for term, frequency in doc_freq.items()
        }

        vectorized: list[_VectorizedNews] = []
        for item, tokens in zip(items, tokenized_docs):
            term_freq = Counter(tokens)
            vector = {term: count * idf[term] for term, count in term_freq.items()}
            norm = math.sqrt(sum(value * value for value in vector.values()))
            vectorized.append(
                _VectorizedNews(item=item, tokens=tokens, vector=vector, norm=norm)
            )
        return vectorized

    @staticmethod
    def _cosine_similarity(left: _VectorizedNews, right: _VectorizedNews) -> float:
        if left.norm == 0.0 or right.norm == 0.0:
            return 0.0

        if len(left.vector) > len(right.vector):
            left, right = right, left

        dot = sum(weight * right.vector.get(term, 0.0) for term, weight in left.vector.items())
        return dot / (left.norm * right.norm)

    def _tokenize(self, text: str) -> list[str]:
        tokens = []
        for token in _TOKEN_PATTERN.findall(text.lower()):
            if len(token) <= 1:
                continue
            if token.isdigit():
                continue
            if token in _STOPWORDS:
                continue
            tokens.append(token)
        return tokens
