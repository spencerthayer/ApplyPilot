"""JD keyword extraction for skill-gap detection."""

import re

__all__ = ["STOPWORDS", "extract_jd_keywords"]

STOPWORDS = frozenset(
    "a about above after again all am an and any are as at be because been before being "
    "below between both but by can could did do does doing down during each few for from "
    "further get got had has have having he her here hers herself him himself his how i if "
    "in into is it its itself just let like make me might more most must my myself no nor "
    "not now of off on once only or other our ours ourselves out over own part per please "
    "put re s same she should so some still such t than that the their theirs them "
    "themselves then there these they this those through to too under until up us very was "
    "we were what when where which while who whom why will with would you your yours "
    "yourself yourselves able also work working experience team role position job company "
    "including include includes using use used based well within across join looking "
    "opportunity responsibilities responsible required requirements preferred qualifications "
    "minimum years year strong knowledge ability skills skill ensure support provide "
    "develop development manage management build building create creating maintain "
    "maintaining etc e g i e "
    "benefits compensation incentive awards perks maternity parental leave health pto "
    "belonging culture associate associates customer customers supplier suppliers "
    "community communities employer equal opportunity inclusive inclusion diversity "
    "valued respected identities opinions styles experiences ideas welcoming "
    "country countries world worldwide global operate operating operations "
    "retailer retail warehouse club membership physical geographic region "
    "floor tower flrs part india chennai bangalore location primary located "
    "outlined listed none below above option options "
    "aim alignment among ago best bring career careers commitment consistent "
    "continuous continuously creating define defining deliver delivering detail "
    "dynamic effectively engaged engagement environment epic expert experts "
    "family feel feels first foreground foster fostering great grow growing "
    "guidance heart helping imagine impact innovative innovate learn learner "
    "led leverage life live lives making meet million millions mindset "
    "new next people person place power powered practices proud purpose "
    "really reinventing rooted sense serve serving shaping start started "
    "today transformative understand unique way welcome".split()
)


def extract_jd_keywords(jd_text: str) -> set[str]:
    """Extract meaningful terms from a JD. Works for any domain."""
    text = re.sub(r"https?://\S+", " ", jd_text)
    text = re.sub(r"\S+@\S+", " ", text)
    text = re.sub(r"[^a-zA-Z+#\s-]", " ", text).lower()
    words = text.split()

    def _is_useful(w: str) -> bool:
        return len(w) >= 3 and w not in STOPWORDS

    bigrams = set()
    for i in range(len(words) - 1):
        if _is_useful(words[i]) and _is_useful(words[i + 1]):
            bigrams.add(f"{words[i]} {words[i + 1]}")
    singles = {w for w in words if _is_useful(w)}
    return bigrams | singles
