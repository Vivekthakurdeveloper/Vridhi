# Vridhi API

FastAPI service for Phase-1 auth, organizations, RBAC, and team invites.

```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
pytest -q
```
