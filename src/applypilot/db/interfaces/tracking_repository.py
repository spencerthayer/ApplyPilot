"""TrackingRepository ABC."""

from abc import ABC, abstractmethod

from applypilot.db.dto import JobDTO, TrackingEmailDTO, TrackingPersonDTO


class TrackingRepository(ABC):
    @abstractmethod
    def get_applied_jobs(self) -> list[JobDTO]: ...

    @abstractmethod
    def update_tracking_status(self, job_url: str, new_status: str) -> bool: ...

    @abstractmethod
    def get_emails(self, job_url: str) -> list[TrackingEmailDTO]: ...

    @abstractmethod
    def get_people(self, job_url: str) -> list[TrackingPersonDTO]: ...

    @abstractmethod
    def get_action_items(self) -> list[JobDTO]: ...

    @abstractmethod
    def get_stats(self) -> dict: ...

    @abstractmethod
    def store_email(self, email: TrackingEmailDTO) -> None: ...

    @abstractmethod
    def store_person(self, person: TrackingPersonDTO) -> None: ...

    @abstractmethod
    def email_exists(self, email_id: str) -> bool: ...

    @abstractmethod
    def update_job_fields(self, job_url: str, fields: dict) -> None: ...

    @abstractmethod
    def get_multi_email_stub_urls(self) -> list[tuple[str, int]]: ...

    @abstractmethod
    def get_stub_email_dicts(self, job_url: str) -> list[dict]: ...

    @abstractmethod
    def move_email_to_job(self, email_id: str, new_job_url: str) -> None: ...

    @abstractmethod
    def delete_orphan_stubs(self) -> int: ...

    @abstractmethod
    def get_all_email_ids(self) -> list[str]: ...
