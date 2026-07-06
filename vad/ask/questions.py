def clarification_questions(ask: str) -> list[str]:
    text = ask.lower()
    questions = []

    if not any(marker in text for marker in ("so that", "success", "acceptance", "done when", "must")):
        questions.append("What success criteria must be proven before this is complete?")
    if not any(marker in text for marker in ("in ", "within ", "only ", "scope", "file", "module", "service")):
        questions.append("What code, service, or module boundaries define the scope?")
    if any(marker in text for marker in ("maybe", "somehow", "unclear", "tbd", "whatever")):
        questions.append("Which ambiguous requirement should be resolved before autonomous work begins?")

    return questions
