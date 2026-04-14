"""AccountRepository ABC."""

from abc import ABC, abstractmethod


class AccountRepository(ABC):
    @abstractmethod
    def get_for_prompt(self) -> dict[str, dict]: ...

    @abstractmethod
    def upsert(
            self,
            site: str,
            domain: str,
            email: str,
            password: str | None = None,
            notes: str | None = None,
            job_url: str | None = None,
    ) -> str: ...

    @abstractmethod
    def delete(self, domain: str) -> int: ...
