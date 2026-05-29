import math
import re
from typing import List, Dict, Tuple

class SimpleBM25:
    """
    A lightweight, high-performance local BM25 implementation for sparse retrieval.
    Runs locally on CPU with zero dependencies, avoiding costly external databases.
    """
    def __init__(self, b: float = 0.75, k1: float = 1.5):
        self.b = b
        self.k1 = k1
        self.doc_len: List[int] = []
        self.avg_doc_len: float = 0.0
        self.doc_freqs: List[Dict[str, int]] = []
        self.idf: Dict[str, float] = {}
        self.documents_metadata: List[Dict] = []
        self.corpus_size = 0

    def tokenize(self, text: str) -> List[str]:
        # Lowercase, remove punctuation, and tokenize
        text = text.lower()
        return re.findall(r'\b[a-z0-9]+\b', text)

    def fit(self, documents: List[Dict[str, any]]):
        """
        Fits BM25 index on a list of chunks: [{"text": "...", "metadata": {...}}]
        """
        self.documents_metadata = documents
        self.corpus_size = len(documents)
        if self.corpus_size == 0:
            return

        self.doc_len = []
        self.doc_freqs = []
        df: Dict[str, int] = {}

        for doc in documents:
            tokens = self.tokenize(doc["text"])
            self.doc_len.append(len(tokens))
            
            # Word frequencies in this document
            freqs: Dict[str, int] = {}
            for token in tokens:
                freqs[token] = freqs.get(token, 0) + 1
            self.doc_freqs.append(freqs)
            
            # Update doc frequency across corpus
            for token in freqs.keys():
                df[token] = df.get(token, 0) + 1

        self.avg_doc_len = sum(self.doc_len) / self.corpus_size

        # Compute IDF
        for word, freq in df.items():
            # BM25 standard IDF with smoothing
            self.idf[word] = math.log((self.corpus_size - freq + 0.5) / (freq + 0.5) + 1.0)

    def score_query(self, query: str) -> List[Tuple[int, float]]:
        """
        Returns scores for all documents given a query as [(doc_index, score)]
        """
        query_tokens = self.tokenize(query)
        scores: List[Tuple[int, float]] = []

        for idx in range(self.corpus_size):
            score = 0.0
            doc_len = self.doc_len[idx]
            freqs = self.doc_freqs[idx]

            for token in query_tokens:
                if token not in freqs:
                    continue
                
                f = freqs[token]
                idf = self.idf.get(token, 0.0)
                
                # BM25 formula
                numerator = idf * f * (self.k1 + 1)
                denominator = f + self.k1 * (1 - self.b + self.b * (doc_len / self.avg_doc_len))
                score += numerator / denominator
                
            scores.append((idx, score))

        return scores

    def search(self, query: str, top_k: int = 20) -> List[Dict]:
        """
        Searches the index and returns the top_k matching chunks with their scores
        """
        if self.corpus_size == 0:
            return []
            
        scores = self.score_query(query)
        # Sort descending by score
        scores.sort(key=lambda x: x[1], reverse=True)
        
        results = []
        for idx, score in scores[:top_k]:
            if score <= 0.0:
                continue
            doc = self.documents_metadata[idx].copy()
            doc["sparse_score"] = score
            results.append(doc)
            
        return results
