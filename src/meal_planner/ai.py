from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AISuggestion:
    title: str
    body: str


class AIPlannerAdapter:
    def suggest_plan_changes(self, context: dict) -> list[AISuggestion]:
        suggestions: list[AISuggestion] = []
        low_feedback = context.get("low_feedback", [])
        unresolved_appliances = context.get("unresolved_appliances", [])
        shopping_notes = context.get("shopping_notes", [])

        if low_feedback:
            suggestions.append(
                AISuggestion(
                    title="Swap out low-rated meals",
                    body="Consider replacing recent low-rated meals with recipes that have similar macros but fewer prep steps.",
                )
            )
        if unresolved_appliances:
            suggestions.append(
                AISuggestion(
                    title="Answer appliance questions",
                    body="Resolve unknown appliances so the planner can unlock more valid recipe options.",
                )
            )
        if shopping_notes:
            suggestions.append(
                AISuggestion(
                    title="Restock frequent items",
                    body="Several staples are running low; restocking them will make next week easier to plan.",
                )
            )

        if not suggestions:
            suggestions.append(
                AISuggestion(
                    title="Keep iterating with feedback",
                    body="As you rate meals and supplements, the planner will prioritize the options that feel easiest and tastiest for you.",
                )
            )
        return suggestions
