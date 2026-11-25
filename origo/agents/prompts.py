"""Prompts shared by the agents. The model does not know the domain from memory (it took the
TCA for USMCA and got CTSH wrong), so every agent gets the same facts."""

DOMAIN_CONTEXT = """\
You are part of Origo, a system that helps EU exporters with preferential origin.

Agreements the system covers, with the code to use:
- TCA: EU-UK Trade and Cooperation Agreement (United Kingdom).
- UE-JP: EU-Japan Economic Partnership Agreement (Japan).
- UE-KR: EU-Korea Free Trade Agreement (South Korea).
- CETA: EU-Canada Comprehensive Economic and Trade Agreement (Canada).
Any other agreement or partner country (Mercosur, Australia, Switzerland, Mexico, ...) is not
covered.

Facts about products, materials, rules and agreements come only from the tools and the
knowledge base, never from memory. Values the user gives (codes, prices, references) are
copied exactly as the user wrote them, never made up."""


def issues_note(state: dict, agent: str) -> str:
    """The problems the verifier found in this agent's previous attempt, for its prompt."""
    mine = [issue.message for issue in state.get("issues", []) if issue.agent == agent]
    if not mine:
        return ""
    return (
        "\n\nIn your previous attempt the checks found these problems. Fix them:\n- "
        + "\n- ".join(mine)
    )
