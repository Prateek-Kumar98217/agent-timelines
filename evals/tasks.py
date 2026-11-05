"""Evaluation tasks with known answers. Every fact is checked against the corpus by a test.

Tasks ask for several lookups, a note and a calculation, so a run takes the 4 to 12 turns that
give forks something to diverge from.
"""

import re
from dataclasses import dataclass

_METHOD = (
    " Look up each fact with a separate kb_search call. Save each fact with scratchpad_write. "
    "Do any arithmetic with the calculator. Then give your final answer."
)


@dataclass(frozen=True)
class Fact:
    title: str
    evidence: str


@dataclass(frozen=True)
class EvalTask:
    id: str
    text: str
    # The final answer must match all of these, after commas are removed.
    required: tuple[str, ...]
    expected_answer: str
    facts: tuple[Fact, ...]
    # Seeded edits, used by the edit sensitivity experiment.
    contradiction: str
    other_query: str
    other_task: str
    paraphrase: str


TASKS = [
    EvalTask(
        id="apollo_gap",
        text="In which years did Apollo 11 and Apollo 17 launch, and how many years apart?"
        + _METHOD,
        required=(r"\b1969\b", r"\b1972\b", r"\b3\b|\bthree\b"),
        expected_answer="Apollo 11 launched in 1969, Apollo 17 in 1972, so 3 years apart.",
        facts=(Fact("Apollo 11", "July 16, 1969"), Fact("Apollo 17", "December 7, 1972")),
        contradiction="Apollo 11: It went up to space on July 16, 1980, carrying three astronauts.",
        other_query="Apollo 13 launch date",
        other_task="In which years did Apollo 8 and Apollo 17 launch?" + _METHOD,
        paraphrase="How many years separated the launches of Apollo 11 and Apollo 17, and which "
        "years were they?" + _METHOD,
    ),
    EvalTask(
        id="peak_gap",
        text="How high are Denali and Aconcagua in metres, and how much higher is the taller one?"
        + _METHOD,
        required=(r"6190|6191", r"6962", r"77[12]"),
        expected_answer="Denali is about 6,190 m, Aconcagua is 6,962 m, about 772 m higher.",
        facts=(Fact("Denali", "6,190.5 m"), Fact("Aconcagua", "6,962 meters")),
        contradiction="Denali: It is exactly 5,000 metres high.",
        other_query="height of Mont Blanc",
        other_task="How high are Mont Blanc and Aconcagua in metres?" + _METHOD,
        paraphrase="Compare the heights of Aconcagua and Denali in metres, and give the difference."
        + _METHOD,
    ),
    EvalTask(
        id="nobel_gap",
        text="In which years did Albert Einstein and Enrico Fermi receive the Nobel Prize in "
        "Physics, and how many years apart were they?" + _METHOD,
        required=(r"\b1921\b", r"\b1938\b", r"\b17\b|\bseventeen\b"),
        expected_answer="Einstein in 1921, Fermi in 1938, so 17 years apart.",
        facts=(
            Fact("Albert Einstein", "Nobel Prize in Physics in 1921"),
            Fact("Enrico Fermi", "Nobel Prize in Physics in 1938"),
        ),
        contradiction="Albert Einstein: He received the Nobel Prize in Physics in 1930.",
        other_query="Niels Bohr Nobel Prize year",
        other_task="In which years did Niels Bohr and Enrico Fermi receive a Nobel Prize?"
        + _METHOD,
        paraphrase="How long after Einstein's Nobel Prize in Physics did Fermi receive his, and "
        "which years were they?" + _METHOD,
    ),
    EvalTask(
        id="probe_gap",
        text="In which years were Voyager 1 and New Horizons launched, and how many years apart?"
        + _METHOD,
        required=(r"\b1977\b", r"\b2006\b", r"\b29\b|twenty-nine"),
        expected_answer="Voyager 1 in 1977, New Horizons in 2006, so 29 years apart.",
        facts=(Fact("Voyager 1", "September 5, 1977"), Fact("New Horizons", "19 January 2006")),
        contradiction="Voyager 1: Voyager 1 was launched on September 5, 1990.",
        other_query="Pioneer 10 launch date",
        other_task="In which years were Hubble and New Horizons launched?" + _METHOD,
        paraphrase="How many years after Voyager 1 did New Horizons launch, and in which years "
        "did they each launch?" + _METHOD,
    ),
    EvalTask(
        id="space_race",
        text="In which years were Sputnik 1 and Vostok 1 launched, and how many years apart?"
        + _METHOD,
        required=(r"\b1957\b", r"\b1961\b", r"\b4\b|\bfour\b"),
        expected_answer="Sputnik 1 in 1957, Vostok 1 in 1961, so 4 years apart.",
        facts=(Fact("Sputnik 1", "4 October 1957"), Fact("Vostok 1", "April 12, 1961")),
        contradiction="Sputnik 1: It was launched on 4 October 1950 at Baikonur Cosmodrome.",
        other_query="first woman in space Vostok 6",
        other_task="In which years were Sputnik 1 and Hubble launched?" + _METHOD,
        paraphrase="How long after Sputnik 1 was Vostok 1 launched, and in which years did they "
        "each launch?" + _METHOD,
    ),
    EvalTask(
        id="telescopes",
        text="In which years were the Hubble and James Webb space telescopes launched, and how "
        "many years apart?" + _METHOD,
        required=(r"\b1990\b", r"\b2021\b", r"\b31\b|thirty-one"),
        expected_answer="Hubble in 1990, James Webb in 2021, so 31 years apart.",
        facts=(
            Fact("Hubble Space Telescope", "April 24, 1990"),
            Fact("James Webb Space Telescope", "25 December 2021"),
        ),
        contradiction="Hubble Space Telescope: It was launched on April 24, 2005.",
        other_query="Voyager 2 launch date",
        other_task="In which years were Hubble and Voyager 1 launched?" + _METHOD,
        paraphrase="How long after the Hubble Space Telescope was James Webb launched, and in "
        "which years did each launch?" + _METHOD,
    ),
    EvalTask(
        id="trip_plan",
        text="Plan a trip with stops in Tokyo, Bangkok and Hanoi. Find the country of each city. "
        "With 4 nights at each stop and 80 dollars a night, what is the total cost?" + _METHOD,
        required=(r"Japan", r"Thailand", r"Vietnam", r"\b960\b"),
        expected_answer="Tokyo is in Japan, Bangkok in Thailand, Hanoi in Vietnam. Total 960.",
        facts=(
            Fact("Tokyo", "city of Japan"),
            Fact("Bangkok", "capital city of Thailand"),
            Fact("Hanoi", "capital of Vietnam"),
        ),
        contradiction="Tokyo: Tokyo is the capital and largest city of South Korea.",
        other_query="capital of Kenya",
        other_task="Plan a trip with stops in Nairobi, Cairo and Abuja. Find the country of each "
        "city. With 4 nights at each stop and 80 dollars a night, what is the total cost?"
        + _METHOD,
        paraphrase="I'm visiting Bangkok, Hanoi and Tokyo for 4 nights each at 80 dollars a night. "
        "Which country is each city in, and what will it cost in total?" + _METHOD,
    ),
    EvalTask(
        id="fact_check",
        text="Fact check these claims and say whether each is true or false, correcting any "
        "false one: Mars is the fourth planet from the Sun. Ganymede is a moon of Saturn. The "
        "Nile is the longest river on Earth." + _METHOD,
        required=(r"Jupiter", r"(?i)false"),
        expected_answer="Mars claim is true. Ganymede claim is false: it orbits Jupiter. "
        "Nile claim is true.",
        facts=(
            Fact("Mars", "fourth planet from the Sun"),
            Fact("Ganymede (moon)", "moons of the planet Jupiter"),
            Fact("Nile", "longest river on Earth"),
        ),
        contradiction="Mars: Mars is the seventh planet from the Sun.",
        other_query="Saturn rings",
        other_task="Fact check these claims and say whether each is true or false: Titan is a "
        "moon of Saturn. Lake Baikal is the deepest lake." + _METHOD,
        paraphrase="Are these true or false? Correct any that are wrong. The Nile is the longest "
        "river on Earth. Ganymede is a moon of Saturn. Mars is the fourth planet from the Sun."
        + _METHOD,
    ),
]
TASKS_BY_ID = {task.id: task for task in TASKS}


def fact_score(task: EvalTask, answer: str | None) -> float:
    """The fraction of required facts in the final answer."""
    if not answer:
        return 0.0
    compact = answer.replace(",", "")
    return sum(1 for pattern in task.required if re.search(pattern, compact)) / len(task.required)
