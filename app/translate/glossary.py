import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Glossary:
    terms: dict[str, str] = field(default_factory=dict)
    fixes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Glossary":
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        terms = {str(k): str(v) for k, v in (data.get("terms") or {}).items()}
        fixes = {str(k): str(v) for k, v in (data.get("fixes") or {}).items()}
        return cls(terms, fixes)

    @property
    def digest(self) -> str:
        """Short hash of the terms, used to detect that the DeepL glossary is outdated."""
        payload = json.dumps(self.terms, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(payload).hexdigest()[:10]

    def post_process(self, text: str, replace_terms: bool) -> str:
        """Apply fixes; with replace_terms, also swap German terms DeepL left as-is."""
        replacements = dict(self.fixes)
        if replace_terms:
            replacements.update(self.terms)
        # Longest first so "Oberbürgermeister" isn't clobbered by a shorter overlapping key.
        for source in sorted(replacements, key=len, reverse=True):
            pattern = rf"(?<!\w){re.escape(source)}(?!\w)"
            text = re.sub(pattern, lambda _m, s=source: replacements[s], text)
        return text
