from enum import StrEnum


class Language(StrEnum):
    CPP = "cpp"
    PYTHON = "python"


class SubmissionStatus(StrEnum):
    QUEUED = "queued"
    JUDGING = "judging"
    DONE = "done"
    FAILED = "failed"


class Verdict(StrEnum):
    ACCEPTED = "AC"
    WRONG_ANSWER = "WA"
    TIME_LIMIT_EXCEEDED = "TLE"
    MEMORY_LIMIT_EXCEEDED = "MLE"
    RUNTIME_ERROR = "RE"
    COMPILATION_ERROR = "CE"
    SYSTEM_ERROR = "SE"
