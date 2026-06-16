import re
from typing import List, Optional

class Redactor:
    def __init__(self, patterns: Optional[List[str]] = None):
        self.patterns = patterns or [
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # Email
            r'\b(?:\d[ -]*?){13,16}\b'  # Basic credit card
        ]
        self._compiled = [re.compile(p) for p in self.patterns]

    def redact(self, text: str) -> str:
        for p in self._compiled:
            text = p.sub('[REDACTED]', text)
        return text
