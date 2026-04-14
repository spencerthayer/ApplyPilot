"""QARepository ABC."""

from abc import ABC, abstractmethod

from applypilot.db.dto import QAKnowledgeDTO


class QARepository(ABC):
    @abstractmethod
    def store(self, question_text: str, question_key: str, answer_text: str, answer_source: str, **kwargs) -> None: ...

    @abstractmethod
    def lookup(self, question_key: str) -> QAKnowledgeDTO | None: ...

    @abstractmethod
    def get_all(self) -> list[QAKnowledgeDTO]: ...
