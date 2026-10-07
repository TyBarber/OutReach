from collections.abc import Callable, Sequence

from outreach import questions
from outreach.models import Profile
from outreach.questions import QUESTIONS, Question, parse_answer


def _format_prompt(question: Question) -> str:
    if question.choices:
        hint = "/".join(question.choices)
    else:
        hint = question.hint

    return f"{question.prompt} ({hint}) " if hint else f"{question.prompt} "


def ask(
        question: Question, 
        input_fn: Callable[[str], str] = input, 
        output_fn: Callable[[str], None] = print, 
):
    while True:
        raw = input_fn(_format_prompt(question))
        try:
            return parse_answer(question, raw)
        except ValueError as err:
            output_fn(f"  ! {err}")


def run_onboarding(
        questions: Sequence[Question] = QUESTIONS, 
        input_fn: Callable[[str], str] = input, 
        output_fn: Callable[[str], None] = print, 
) -> Profile:
    answers = {q.key: ask(q, input_fn, output_fn) for q in questions}
    return Profile.from_dict(answers)