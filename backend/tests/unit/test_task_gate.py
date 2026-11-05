import pytest

from app.domain.task_gate import REFUSAL_CODING, REFUSAL_LONG, task_problem


@pytest.mark.parametrize(
    "task",
    [
        "Write a Python script to parse a CSV file",
        "write me a function that reverses a string",
        "Can you implement a binary search algorithm in Rust?",
        "Fix this code for me",
        "debug my javascript",
        "Build a website for my bakery",
        "Generate a SQL query for monthly sales",
        "How do I sort a list in Python?",
        "Why do I get a null pointer here",
        "Create unit tests for my login page",
        "```\nprint('hi')\n```",
        "def add(a, b): return a + b",
    ],
)
def test_coding_tasks_are_refused(task: str) -> None:
    assert task_problem(task) == REFUSAL_CODING


@pytest.mark.parametrize(
    "task",
    [
        "Write a 50,000 words report on the Moon",
        "write 10000 words about rivers",
        "Write a novel about Mars",
        "Draft a short book on space travel",
        "Monitor the news and keep checking for updates",
        "Check this every hour",
    ],
)
def test_long_running_tasks_are_refused(task: str) -> None:
    assert task_problem(task) == REFUSAL_LONG


@pytest.mark.parametrize(
    "task",
    [
        "How tall is Mount Everest?",
        "Fact check: Marie Curie won two Nobel Prizes.",
        "Plan a 3-stop trip of Tokyo, Bangkok and Hanoi with a budget of 2000 dollars.",
        "What language is spoken in Java, Indonesia?",
        "How long can a python snake grow?",
        "Is the Rust Belt in the United States?",
        "Which class of star is the Sun?",
        "What is the function of the liver?",
        "Who created the theory of relativity?",
        "Find the capital of Australia and write the answer to the scratchpad.",
        "Create a packing list for a week in Iceland.",
        "Fix the dates in this plan: arrive Monday, leave Sunday.",
        "Compare the program of Apollo with the Soviet space efforts.",
        "How many years before 2000 was Apollo 11?",
        "Summarise the Wikipedia article on Saturn in 300 words.",
    ],
)
def test_ordinary_tasks_pass(task: str) -> None:
    assert task_problem(task) is None
