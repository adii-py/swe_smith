"""Quality scoring for generated bugs."""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class QualityScore:
    """Quality score breakdown."""
    realism: float
    complexity: float
    minimality: float
    testability: float
    overall: float
    details: Dict[str, str]


class QualityScorer:
    """Score generated bugs for quality."""

    def score(self, bug) -> float:
        """Score a GeneratedBug (new format) and return overall score."""
        # Convert bug to patch format for scoring
        from ..generator.mutation_generator import GeneratedBug

        if isinstance(bug, GeneratedBug):
            # Create a synthetic patch from modified files
            patch_lines = []
            for file_path in bug.affected_files:
                if file_path in bug.original_files and file_path in bug.modified_files:
                    orig = bug.original_files[file_path]
                    mod = bug.modified_files[file_path]
                    patch_lines.append(f"diff --git a/{file_path} b/{file_path}")
                    patch_lines.append("--- a/file")
                    patch_lines.append("+++ b/file")
                    # Simple diff representation
                    for line in orig.split('\n'):
                        patch_lines.append(f" {line}")
                    for line in mod.split('\n'):
                        patch_lines.append(f"+{line}")

            patch = '\n'.join(patch_lines)
            explanation = bug.explanation
            affected_files = bug.affected_files

            score = self.score_bug(patch, explanation, affected_files)
            return score.overall

        # Fallback for old format
        return 0.7

    def score_bug(
        self,
        patch: str,
        explanation: str,
        affected_files: List[str],
    ) -> QualityScore:
        """Calculate quality scores for a bug."""

        scores = {
            "realism": self._score_realism(patch, explanation),
            "complexity": self._score_complexity(patch),
            "minimality": self._score_minimality(patch, affected_files),
            "testability": self._score_testability(patch, explanation),
        }

        # Calculate overall (weighted average)
        weights = {"realism": 0.3, "complexity": 0.25, "minimality": 0.25, "testability": 0.2}
        overall = sum(scores[k] * weights[k] for k in scores)

        details = self._generate_details(patch, explanation, scores)

        return QualityScore(
            realism=scores["realism"],
            complexity=scores["complexity"],
            minimality=scores["minimality"],
            testability=scores["testability"],
            overall=overall,
            details=details,
        )

    def _score_realism(self, patch: str, explanation: str) -> float:
        """Score how realistic the bug is."""
        score = 0.5

        # Check for realistic bug patterns
        realistic_patterns = [
            # Off-by-one errors
            ">=", ">",
            "<=", "<",
            # Logic errors
            "==", "!=",
            "&&", "||",
            # Missing checks
            "?",
            "ok()",
            # Error handling
            "unwrap",
            "expect",
        ]

        for pattern in realistic_patterns:
            if pattern in patch:
                score += 0.05

        # Check explanation quality
        if explanation and len(explanation) > 20:
            score += 0.1

        return min(score, 1.0)

    def _score_complexity(self, patch: str) -> float:
        """Score bug complexity (reasoning required)."""
        score = 0.3

        # Multi-line changes are more complex
        lines_changed = patch.count('\n-') + patch.count('\n+')
        if lines_changed > 2:
            score += 0.2
        if lines_changed > 5:
            score += 0.2

        # Multiple files increases complexity
        files = patch.count('diff --git')
        if files > 1:
            score += 0.15

        # Conditional changes are complex
        if 'if ' in patch or 'match ' in patch:
            score += 0.15

        return min(score, 1.0)

    def _score_minimality(self, patch: str, affected_files: List[str]) -> float:
        """Score how minimal the bug is."""
        score = 0.5

        # Fewer files is better
        if len(affected_files) == 1:
            score += 0.3
        elif len(affected_files) <= 3:
            score += 0.15

        # Smaller patches are better
        lines = len(patch.split('\n'))
        if lines < 30:
            score += 0.2
        elif lines < 50:
            score += 0.1

        return min(score, 1.0)

    def _score_testability(self, patch: str, explanation: str) -> float:
        """Score how testable the bug is."""
        score = 0.4

        # Behavioral bugs are more testable
        behavioral_keywords = [
            "filter", "permission", "check", "validate",
            "return", "error", "none", "some", "ok", "err",
        ]

        for kw in behavioral_keywords:
            if kw in explanation.lower():
                score += 0.1
                break

        # State changes are testable
        if 'mut ' in patch or '= ' in patch:
            score += 0.15

        return min(score, 1.0)

    def _generate_details(
        self,
        patch: str,
        explanation: str,
        scores: Dict[str, float],
    ) -> Dict[str, str]:
        """Generate human-readable quality details."""
        details = {}

        # Realism assessment
        if scores["realism"] > 0.7:
            details["realism"] = "High - typical developer mistake pattern"
        elif scores["realism"] > 0.5:
            details["realism"] = "Medium - somewhat realistic"
        else:
            details["realism"] = "Low - may look artificial"

        # Complexity assessment
        if scores["complexity"] > 0.7:
            details["complexity"] = "High - requires careful reasoning to fix"
        elif scores["complexity"] > 0.5:
            details["complexity"] = "Medium - some reasoning required"
        else:
            details["complexity"] = "Low - trivial fix"

        # Minimality assessment
        if scores["minimality"] > 0.7:
            details["minimality"] = "High - focused, minimal change"
        else:
            details["minimality"] = "Lower - could be more focused"

        # Testability assessment
        if scores["testability"] > 0.6:
            details["testability"] = "Good - behavior change detectable"
        else:
            details["testability"] = "Weak - may be hard to test"

        return details

    def should_accept(self, score: QualityScore, threshold: float = 0.6) -> bool:
        """Determine if bug meets quality threshold."""
        return score.overall >= threshold
