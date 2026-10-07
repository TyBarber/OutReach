from outreach.onboarding import run_onboarding


def scripted(answers):
    it = iter(answers)
    return lambda _prompt: next(it)


def test_full_run_builds_profile():
    profile = run_onboarding(
        input_fn=scripted([
            "Ty", "BSc", "IT", "Support, SysAdmin", "linux, aws",
            "remote, async", "France", "direct", "",
        ]),
        output_fn=lambda _msg: None,
    )
    assert profile.target_roles == ["Support", "SysAdmin"]
    assert profile.tone == "direct"
    assert profile.resume_path is None


def test_invalid_answer_reprompts():
    messages = []
    profile = run_onboarding(
        input_fn=scripted([
            "", "Ty", "BSc", "IT", "Support", "linux",
            "remote", "France", "loud", "warm", "",
        ]),
        output_fn=messages.append,
    )
    assert profile.name == "Ty"
    assert profile.tone == "warm"
    assert len(messages) == 2