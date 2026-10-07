import os
import tempfile
import pytest

# Ensure pytest runs against an isolated temporary SQLite database file
# so unit tests never pollute the production Client_RumahSakit_Template.db.
test_db_file = tempfile.NamedTemporaryFile(suffix="_test.db", delete=False)
test_db_file.close()

os.environ["DATABASE_URL"] = f"sqlite:///{test_db_file.name}"


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_db():
    yield
    try:
        if os.path.exists(test_db_file.name):
            os.remove(test_db_file.name)
    except Exception:
        pass
