"""Idempotency keys stop double-clicks and network retries from creating duplicates.

The client generates a UUID when a form opens and sends it as the Idempotency-Key
header on create. The key is stored on the created row under a unique
(user_id, idempotency_key) constraint, so a repeated request returns the
original record instead of inserting a second one, even if both arrive at once.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Header

IdempotencyKey = Annotated[UUID | None, Header(alias="Idempotency-Key")]
