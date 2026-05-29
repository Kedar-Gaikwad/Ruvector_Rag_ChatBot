import re
import pandas as pd
from io import StringIO
from typing import List, Dict, Any

class SmartFinancialChunker:
    """
    An intelligent chunker custom-tailored for financial documents.
    Preserves tabular rows, respects sentence boundaries, and injects 
    contextual metadata (e.g. document name, section headers) into every chunk
    to maximize retrieval accuracy while keeping chunk sizes small.
    """
    
    def __init__(self, chunk_size: int = 600, overlap: int = 100):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def clean_text(self, text: str) -> str:
        # Standardize whitespace and remove non-printable chars
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def is_table_row(self, line: str) -> bool:
        # Check if line looks like a table row (multiple columns separated by tabs, commas, pipes, or spaces)
        line = line.strip()
        if not line:
            return False
        # Pipes
        if line.count('|') >= 2:
            return True
        # Tabs or commas with numbers
        if line.count('\t') >= 1 or line.count(',') >= 2:
            # Check if there are numbers representing financial metrics
            if re.search(r'\d+', line):
                return True
        # Double spaces indicating columns
        if len(re.split(r'  +', line)) >= 3:
            return True
        return False

    def parse_table_from_text(self, text: str) -> List[Dict[str, Any]]:
        """
        Attempts to detect, extract, and clean tabular blocks from text,
        formatting them as individual context-rich rows.
        """
        lines = text.split('\n')
        chunks = []
        in_table = False
        table_lines = []
        table_start_index = 0
        
        for i, line in enumerate(lines):
            is_row = self.is_table_row(line)
            
            if is_row and not in_table:
                in_table = True
                table_lines = [line]
                table_start_index = max(0, i - 2) # Capture preceding headers (usually 1-2 lines above)
            elif is_row and in_table:
                table_lines.append(line)
            elif not is_row and in_table:
                # Table ended. Process table lines
                in_table = False
                headers = [lines[j].strip() for j in range(table_start_index, table_start_index + (i - len(table_lines) - table_start_index))]
                headers_str = " | ".join([h for h in headers if h])
                
                # Create detailed chunks for each table row with header context
                table_context = f"Table Header Context: {headers_str}" if headers_str else "Financial Table Data"
                for row in table_lines:
                    if row.strip():
                        chunks.append({
                            "text": f"{table_context}\nRow Data: {row.strip()}",
                            "metadata": {"type": "table_row", "header": headers_str[:100]}
                        })
                table_lines = []
        
        # Catch lingering table at end of document
        if in_table and table_lines:
            headers = [lines[j].strip() for j in range(table_start_index, max(0, len(lines) - len(table_lines)))]
            headers_str = " | ".join([h for h in headers if h])
            table_context = f"Table Header Context: {headers_str}" if headers_str else "Financial Table Data"
            for row in table_lines:
                if row.strip():
                    chunks.append({
                        "text": f"{table_context}\nRow Data: {row.strip()}",
                        "metadata": {"type": "table_row", "header": headers_str[:100]}
                    })
                    
        return chunks

    def chunk_document(self, text: str, doc_name: str) -> List[Dict[str, Any]]:
        """
        Main entry point for chunking. Segregates tabular data from prose text,
        chunks prose text using sliding semantic windows, and combines them.
        """
        cleaned_text = self.clean_text(text)
        
        # 1. First, extract tabular chunks
        table_chunks = self.parse_table_from_text(cleaned_text)
        
        # 2. Next, extract prose chunks
        # To avoid double-ingesting tables, we can filter out heavy table rows from the prose engine, 
        # but for simplicity and safety, we run sentence-based chunking on the whole document.
        prose_chunks = []
        
        # Split text into paragraphs
        paragraphs = cleaned_text.split('\n\n')
        current_chunk = []
        current_len = 0
        
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
                
            # If paragraph contains heavy table data, we skip or treat lightly
            if paragraph.count('|') >= 4 and len(paragraph) < 1000:
                continue # Tabular chunker will handle this cleaner
                
            para_len = len(paragraph)
            
            if current_len + para_len > self.chunk_size:
                # Save existing chunk
                if current_chunk:
                    chunk_text = "\n\n".join(current_chunk)
                    prose_chunks.append({
                        "text": f"Document: {doc_name}\nContent:\n{chunk_text}",
                        "metadata": {"type": "prose"}
                    })
                
                # Handle paragraph overlap
                # If paragraph itself is too large, we split it into sentences
                if para_len > self.chunk_size:
                    sentences = re.split(r'(?<=[.!?]) +', paragraph)
                    sub_chunk = []
                    sub_len = 0
                    for sentence in sentences:
                        sub_len += len(sentence)
                        sub_chunk.append(sentence)
                        if sub_len > self.chunk_size:
                            prose_chunks.append({
                                "text": f"Document: {doc_name}\nContent:\n{' '.join(sub_chunk)}",
                                "metadata": {"type": "prose"}
                            })
                            # Overlap sentences
                            sub_chunk = sub_chunk[-2:] if len(sub_chunk) >= 2 else sub_chunk
                            sub_len = sum(len(s) for s in sub_chunk)
                    current_chunk = sub_chunk
                    current_len = sub_len
                else:
                    current_chunk = [paragraph]
                    current_len = para_len
            else:
                current_chunk.append(paragraph)
                current_len += para_len + 2 # account for newline join
                
        # Append final chunk
        if current_chunk:
            chunk_text = "\n\n".join(current_chunk)
            prose_chunks.append({
                "text": f"Document: {doc_name}\nContent:\n{chunk_text}",
                "metadata": {"type": "prose"}
            })
            
        # 3. Add document metadata to all chunks and merge
        all_chunks = []
        for c in table_chunks + prose_chunks:
            meta = c["metadata"]
            meta["source"] = doc_name
            all_chunks.append({
                "text": f"Source: {doc_name}\n{c['text']}",
                "metadata": meta
            })
            
        return all_chunks
