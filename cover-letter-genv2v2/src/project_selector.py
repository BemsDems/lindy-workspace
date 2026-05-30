"""Project selection with diversity penalty and deterministic scoring."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from .analyzer import _score_project_for_vacancy
from .facts import CanonicalFacts
from .models import Vacancy


_DEFAULT_SELECTOR: Optional[ProjectSelector] = None


def get_default_selector() -> ProjectSelector:
    """Singleton accessor for the default project selector."""
    global _DEFAULT_SELECTOR
    if _DEFAULT_SELECTOR is None:
        _DEFAULT_SELECTOR = ProjectSelector()
    return _DEFAULT_SELECTOR


class ProjectSelector:
    """Selects the best project for a vacancy with diversity penalty."""

    def __init__(self, window_size: int = 5) -> None:
        self._recent_picks: list[str] = []
        self._window_size = window_size

    def select(
        self,
        vacancy: Vacancy,
        facts: CanonicalFacts,
        llm_selected: Optional[str] = None,
    ) -> Tuple[str, Optional[str]]:
        """Select the best project for the vacancy with diversity penalty.

        Args:
            vacancy: Vacancy to match against.
            facts: Candidate's projects and skills.
            llm_selected: Optional project name suggested by LLM (prioritized if deterministic score is close).

        Returns:
            Tuple of (selected_project_name, reason).
        """
        scores: Dict[str, float] = {}

        for project_name, project in facts.projects.items():
            base_score = _score_project_for_vacancy(vacancy, project)
            penalty = self._recent_picks.count(project_name) * 0.5
            scores[project_name] = base_score - penalty

        if not scores:
            return next(iter(facts.projects.keys())), "No projects available"

        best_name = max(scores, key=scores.get)
        best_score = scores[best_name]

        # Override with LLM suggestion if it's close in score
        if llm_selected and llm_selected in facts.projects:
            llm_score = scores.get(llm_selected, 0.0)
            if llm_score >= best_score - 2.0:  # Allow small score difference
                best_name = llm_selected
                reason = f"LLM suggestion {llm_selected} accepted (score difference <= 2.0)"
            else:
                reason = f"Deterministic best: {best_name} (score {best_score:.1f}), LLM suggestion {llm_selected} rejected (score {llm_score:.1f})"
        else:
            reason = f"Deterministic best: {best_name} (score {best_score:.1f})"

        # Update recent picks
        self._recent_picks.append(best_name)
        if len(self._recent_picks) > self._window_size:
            self._recent_picks.pop(0)

        return best_name, reason