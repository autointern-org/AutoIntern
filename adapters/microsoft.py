from __future__ import annotations

import requests

from adapters.base import Job
from adapters.eightfold import EightfoldAdapter, EightfoldBoard


class MicrosoftAdapter:
    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self._inner = EightfoldAdapter(
            [
                EightfoldBoard(
                    company="microsoft",
                    host="apply.careers.microsoft.com",
                    domain="microsoft.com",
                    extra_params={
                        "location": "United States",
                        "filter_employment_type": "internship",
                    },
                )
            ],
            timeout=timeout,
            session=session,
        )

    def fetch(self) -> list[Job]:
        return self._inner.fetch()
