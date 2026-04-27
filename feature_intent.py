"""Feature Intent Extractor - understands what developers intended to name features.

This goes beyond simple file/module names to extract the actual feature names
developers use in docstrings, comments, and code organization.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, List


class FeatureIntentExtractor:
    """Extracts developer-intended feature names from source code."""

    # Patterns that developers use to mark features
    FEATURE_PATTERNS = [
        r'#\s*Feature:\s*(.+?)(?:\n|$)',
        r'"""\s*Feature:\s*(.+?)(?:\n|$)',
        r"'''\s*Feature:\s*(.+?)(?:\n|$)",
        r'@feature\(["\'](.+?)["\']\)',
        r'#\s*@feature\s+(.+?)(?:\n|$)',
    ]

    # Section markers that indicate feature boundaries
    SECTION_MARKERS = [
        r'#\s*[-=]{3,}\s*\n#\s*(.+?)\s*\n#\s*[-=]{3,}',
        r'"""\s*(.+?)\s*[-=]{3,}',
    ]

    def __init__(self) -> None:
        self._cache: Dict[str, dict] = {}

    def extract_from_file(self, file_path: str, source_code: str) -> dict:
        """Extract feature intent from a single file.

        Returns:
            {
                "primary_feature": str,  # The main feature this file implements
                "alternative_names": [str],  # Other names found
                "module_docstring": str,
                "confidence": float,  # 0.0-1.0
            }
        """
        if file_path in self._cache:
            return self._cache[file_path]

        result = {
            "primary_feature": "",
            "alternative_names": [],
            "module_docstring": "",
            "confidence": 0.0,
        }

        # Try explicit feature markers first (highest confidence)
        explicit = self._find_explicit_markers(source_code)
        if explicit:
            result["primary_feature"] = explicit[0]
            result["alternative_names"] = explicit[1:]
            result["confidence"] = 0.95
            self._cache[file_path] = result
            return result

        # Try module docstring
        module_doc = self._extract_module_docstring(source_code)
        if module_doc:
            result["module_docstring"] = module_doc
            # Try to infer feature from docstring
            inferred = self._infer_from_docstring(module_doc, file_path)
            if inferred:
                result["primary_feature"] = inferred
                result["confidence"] = 0.8
                self._cache[file_path] = result
                return result

        # Try class docstrings (Python)
        if file_path.endswith('.py'):
            class_features = self._extract_python_classes(source_code)
            if class_features:
                result["primary_feature"] = class_features[0]
                result["alternative_names"] = class_features[1:]
                result["confidence"] = 0.7
                self._cache[file_path] = result
                return result

        # Fallback: use filename
        result["primary_feature"] = self._filename_to_feature(file_path)
        result["confidence"] = 0.4
        self._cache[file_path] = result
        return result

    def _find_explicit_markers(self, source_code: str) -> List[str]:
        """Find explicit feature markers in code."""
        names = []
        for pattern in self.FEATURE_PATTERNS:
            matches = re.findall(pattern, source_code, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                if match and match.strip():
                    names.append(match.strip())
        return names

    def _extract_module_docstring(self, source_code: str) -> str:
        """Extract the module-level docstring."""
        # Simple regex approach
        # Match """ ... """ or ''' ... ''' at the start (after optional whitespace/comments)
        match = re.search(
            r'^(?:\s*(?:#.*?\n)*)?\s*("""(.*?)"""|\'\'\'(.*?)\'\'\')',
            source_code,
            re.DOTALL | re.MULTILINE
        )
        if match:
            return (match.group(2) or match.group(3) or "").strip()
        return ""

    def _infer_from_docstring(self, docstring: str, file_path: str) -> str:
        """Try to infer a feature name from module docstring.

        Looks for patterns like:
        - "Authentication module..."
        - "This module handles user authentication"
        - "Provides the graph visualization features"
        """
        # Look for "X module/component/system"
        patterns = [
            r'^\s*(?:This\s+module\s+(?:handles?|provides?|implements?|contains?)\s+)?(.+?)(?:\s+module|\s+component|\s+system|\s+features?)',
            r'^\s*(.+?)(?:\s+-\s+|\s*:\s*)',  # "Feature Name - description"
        ]

        for pattern in patterns:
            match = re.search(pattern, docstring, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                # Clean up the name
                name = name.title()
                if len(name) > 3 and len(name) < 50:
                    return name

        return ""

    def _extract_python_classes(self, source_code: str) -> List[str]:
        """Extract class names from Python source."""
        try:
            tree = ast.parse(source_code)
            classes = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    # Skip internal/private classes
                    if not node.name.startswith('_'):
                        # Convert CamelCase to readable name
                        readable = self._camel_to_readable(node.name)
                        classes.append(readable)
            return classes
        except SyntaxError:
            return []

    def _camel_to_readable(self, name: str) -> str:
        """Convert CamelCase to readable feature name.

        Examples:
            TrellisEngine -> "Trellis Engine"
            GraphStore -> "Graph Store"
            AuthManager -> "Auth Manager"
        """
        # Insert space before capital letters
        readable = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
        # Handle consecutive capitals (e.g., "URLParser" -> "URL Parser")
        readable = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', readable)
        return readable.strip()

    def _filename_to_feature(self, file_path: str) -> str:
        """Convert filename to feature name (fallback)."""
        name = Path(file_path).stem
        # Remove common suffixes
        for suffix in ['_manager', '_service', '_controller', '_handler', '_utils']:
            if name.endswith(suffix):
                name = name[:-len(suffix)]
                break

        # Convert to title case
        return name.replace('_', ' ').replace('-', ' ').title()

    def merge_feature_intents(
        self,
        file_intents: Dict[str, dict],
    ) -> Dict[str, List[str]]:
        """Merge intents from multiple files into consolidated features.

        Returns:
            {feature_name: [file_paths]}
        """
        features: Dict[str, List[str]] = {}

        for file_path, intent in file_intents.items():
            primary = intent.get("primary_feature", "")
            if not primary:
                continue

            # Normalize: check if this is similar to an existing feature
            normalized = self._normalize_feature_name(primary)
            matched = False

            for existing_name, files in list(features.items()):
                if self._feature_names_match(normalized, existing_name):
                    features[existing_name].append(file_path)
                    matched = True
                    break

            if not matched:
                features[normalized] = [file_path]

        return features

    def _normalize_feature_name(self, name: str) -> str:
        """Normalize a feature name for comparison."""
        # Remove common suffixes
        name = re.sub(r'\s+(module|component|system|feature)$', '', name, flags=re.IGNORECASE)
        return name.strip().lower()

    def _feature_names_match(self, name1: str, name2: str) -> bool:
        """Check if two feature names refer to the same thing."""
        n1 = self._normalize_feature_name(name1)
        n2 = self._normalize_feature_name(name2)

        if n1 == n2:
            return True

        # Check if one contains the other
        if n1 in n2 or n2 in n1:
            return True

        # Check word overlap
        words1 = set(n1.split())
        words2 = set(n2.split())
        if words1 and words2:
            overlap = words1 & words2
            if len(overlap) >= min(len(words1), len(words2)) * 0.5:
                return True

        return False
