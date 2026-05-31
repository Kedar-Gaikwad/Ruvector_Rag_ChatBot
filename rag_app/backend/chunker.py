import re
from typing import List, Dict, Any, Optional, Tuple


class SmartFinancialChunker:
    """
    Chunker for financial documents AND structured forms (W-9, 1099, etc.).

    For forms: chunks each page separately so filled values on page 1
    don't get buried under 5 pages of instructions.

    For reports: standard sliding-window prose + table chunking.
    """

    def __init__(self, chunk_size: int = 600, overlap: int = 100):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def clean_text(self, text: str) -> str:
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def is_form_document(self, text: str) -> bool:
        """Detect structured tax/legal forms vs narrative financial reports."""
        form_patterns = [
            r'\bW-9\b', r'\bW9\b', r'\b1099\b', r'\b1040\b', r'\bW-2\b',
            r'\bTIN\b', r'\bSSN\b', r'\bEIN\b',
            r'Taxpayer Identification',
            r'Social security number',
            r'Employer identification number',
            r'Line \d+',
            r'Part I\b', r'Part II\b',
            r'Request for Taxpayer',
        ]
        matches = sum(1 for p in form_patterns if re.search(p, text, re.IGNORECASE))
        return matches >= 2

    def is_table_row(self, line: str) -> bool:
        line = line.strip()
        if not line:
            return False
        if line.count('|') >= 2:
            return True
        if line.count('\t') >= 1 or line.count(',') >= 2:
            if re.search(r'\d+', line):
                return True
        if len(re.split(r'  +', line)) >= 3:
            return True
        return False

    def chunk_page(self, page_text: str, doc_name: str, page_num: int) -> List[Dict[str, Any]]:
        """
        Chunk a single page into overlapping windows.
        Every line is kept — no filtering — so short form values are preserved.
        """
        lines = [l for l in page_text.split('\n') if l.strip()]
        chunks = []
        current_lines: List[str] = []
        current_len = 0

        for line in lines:
            line_len = len(line)
            if current_len + line_len > self.chunk_size and current_lines:
                chunk_text = '\n'.join(current_lines)
                chunks.append({
                    "text": f"Source: {doc_name}\nPage: {page_num}\nDocument: {doc_name}\nContent:\n{chunk_text}",
                    "metadata": {
                        "type": "form_field",
                        "source": doc_name,
                        "page": page_num
                    }
                })
                # 3-line overlap so field label + value don't get split across chunks
                current_lines = current_lines[-3:]
                current_len = sum(len(l) for l in current_lines)

            current_lines.append(line)
            current_len += line_len

        if current_lines:
            chunk_text = '\n'.join(current_lines)
            chunks.append({
                "text": f"Source: {doc_name}\nPage: {page_num}\nDocument: {doc_name}\nContent:\n{chunk_text}",
                "metadata": {
                    "type": "form_field",
                    "source": doc_name,
                    "page": page_num
                }
            })

        return chunks

    def parse_table_from_text(self, text: str) -> List[Dict[str, Any]]:
        lines = text.split('\n')
        chunks = []
        in_table = False
        table_lines: List[str] = []
        table_start_index = 0

        for i, line in enumerate(lines):
            is_row = self.is_table_row(line)
            if is_row and not in_table:
                in_table = True
                table_lines = [line]
                table_start_index = max(0, i - 2)
            elif is_row and in_table:
                table_lines.append(line)
            elif not is_row and in_table:
                in_table = False
                headers = [lines[j].strip() for j in range(
                    table_start_index,
                    table_start_index + (i - len(table_lines) - table_start_index)
                )]
                headers_str = " | ".join([h for h in headers if h])
                table_context = f"Table Header Context: {headers_str}" if headers_str else "Financial Table Data"
                for row in table_lines:
                    if row.strip():
                        chunks.append({
                            "text": f"{table_context}\nRow Data: {row.strip()}",
                            "metadata": {"type": "table_row", "header": headers_str[:100]}
                        })
                table_lines = []

        if in_table and table_lines:
            headers = [lines[j].strip() for j in range(
                table_start_index, max(0, len(lines) - len(table_lines))
            )]
            headers_str = " | ".join([h for h in headers if h])
            table_context = f"Table Header Context: {headers_str}" if headers_str else "Financial Table Data"
            for row in table_lines:
                if row.strip():
                    chunks.append({
                        "text": f"{table_context}\nRow Data: {row.strip()}",
                        "metadata": {"type": "table_row", "header": headers_str[:100]}
                    })

        return chunks

    def chunk_document(
        self,
        text: str,
        doc_name: str,
        page_contents: Optional[List[Tuple[int, str]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Main entry point.

        page_contents: list of (page_number, page_text) tuples from pypdf.
        When provided and the document is a form, each page is chunked
        independently so page-1 filled values aren't buried by later pages.
        """
        cleaned_text = self.clean_text(text)

        # Form path: chunk each page separately
        if self.is_form_document(cleaned_text) and page_contents:
            all_chunks = []
            for page_num, page_text in page_contents:
                cleaned_page = self.clean_text(page_text)
                page_chunks = self.chunk_page(cleaned_page, doc_name, page_num)
                all_chunks.extend(page_chunks)
            return all_chunks

        # Standard financial report path
        table_chunks = self.parse_table_from_text(cleaned_text)
        prose_chunks = []
        paragraphs = cleaned_text.split('\n\n')
        current_chunk: List[str] = []
        current_len = 0

        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue

            # Only skip paragraphs that are pure pipe-table data
            if paragraph.count('|') >= 6 and len(paragraph) < 1000:
                continue

            para_len = len(paragraph)

            if current_len + para_len > self.chunk_size:
                if current_chunk:
                    chunk_text = "\n\n".join(current_chunk)
                    prose_chunks.append({
                        "text": f"Document: {doc_name}\nContent:\n{chunk_text}",
                        "metadata": {"type": "prose"}
                    })

                if para_len > self.chunk_size:
                    sentences = re.split(r'(?<=[.!?]) +', paragraph)
                    sub_chunk: List[str] = []
                    sub_len = 0
                    for sentence in sentences:
                        sub_len += len(sentence)
                        sub_chunk.append(sentence)
                        if sub_len > self.chunk_size:
                            prose_chunks.append({
                                "text": f"Document: {doc_name}\nContent:\n{' '.join(sub_chunk)}",
                                "metadata": {"type": "prose"}
                            })
                            sub_chunk = sub_chunk[-2:] if len(sub_chunk) >= 2 else sub_chunk
                            sub_len = sum(len(s) for s in sub_chunk)
                    current_chunk = sub_chunk
                    current_len = sub_len
                else:
                    current_chunk = [paragraph]
                    current_len = para_len
            else:
                current_chunk.append(paragraph)
                current_len += para_len + 2

        if current_chunk:
            chunk_text = "\n\n".join(current_chunk)
            prose_chunks.append({
                "text": f"Document: {doc_name}\nContent:\n{chunk_text}",
                "metadata": {"type": "prose"}
            })

        all_chunks = []
        for c in table_chunks + prose_chunks:
            meta = c["metadata"]
            meta["source"] = doc_name
            all_chunks.append({
                "text": f"Source: {doc_name}\n{c['text']}",
                "metadata": meta
            })

        return all_chunks
