"""SqliteAccountRepository."""

from __future__ import annotations

from datetime import datetime, timezone

from applypilot.db.interfaces.account_repository import AccountRepository
from applypilot.db.sqlite.base_repo import SqliteBaseRepo


class SqliteAccountRepository(SqliteBaseRepo, AccountRepository):
    def get_for_prompt(self) -> dict[str, dict]:
        rows = self._conn.execute(
            "SELECT domain, email, password, notes FROM accounts ORDER BY created_at DESC"
        ).fetchall()
        result = {}
        for r in rows:
            d = r["domain"]
            if d not in result:
                login_method = None
                notes = r["notes"] or ""
                if "login_method:" in notes:
                    login_method = notes.split("login_method:")[1].strip().split()[0]
                result[d] = {"email": r["email"], "password": r["password"], "login_method": login_method}
        return result

    def upsert(
            self,
            site: str,
            domain: str,
            email: str,
            password: str | None = None,
            notes: str | None = None,
            job_url: str | None = None,
    ) -> str:
        now = datetime.now(timezone.utc).isoformat()
        existing = self._conn.execute("SELECT id FROM accounts WHERE domain=? AND email=?", (domain, email)).fetchone()
        if existing:
            updates, params = [], []
            if password is not None:
                updates.append("password=?")
                params.append(password)
            if notes is not None:
                updates.append("notes=?")
                params.append(notes)
            if job_url is not None:
                updates.append("job_url=?")
                params.append(job_url)
            if updates:
                params.append(existing["id"])

                def _do():
                    self._conn.execute(f"UPDATE accounts SET {', '.join(updates)} WHERE id=?", params)

                self._write(_do)
            return "updated"
        else:

            def _do():
                self._conn.execute(
                    "INSERT INTO accounts (site, domain, email, password, notes, created_at) VALUES (?,?,?,?,?,?)",
                    (site or domain.split(".")[0], domain, email, password, notes, now),
                )

            self._write(_do)
            return "created"

    def delete(self, domain: str) -> int:
        cur = self._conn.execute("DELETE FROM accounts WHERE domain=?", (domain,))
        self._conn.commit()
        return cur.rowcount
