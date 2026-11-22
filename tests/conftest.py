import pytest

from config import PARTNER_USER_ID
from utils.memory_rules import LOCKED_NAMES


@pytest.fixture(autouse=True)
def fake_locked_name():
    saved = dict(LOCKED_NAMES)
    LOCKED_NAMES.clear()
    LOCKED_NAMES["quinn"] = PARTNER_USER_ID
    yield
    LOCKED_NAMES.clear()
    LOCKED_NAMES.update(saved)
