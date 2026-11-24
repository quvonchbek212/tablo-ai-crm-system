from app.question_plan import QuestionPlan, Requirement


async def grounded_plan(question, schema_hints=None):
    """Isolate routing tests from the remote model; planner has separate tests."""
    item = Requirement(meaning=question, evidence=question)
    return QuestionPlan(entity=item, measure=item, operation="complex", dimensions=[],
                        filters=[], time=[], ordering=[], limit=None, limit_evidence=None,
                        ambiguities=[])
