# Profiles API

A FastAPI service that accepts a name, enriches it with gender, age, and nationality data from three external APIs, stores the result in SQLite, and exposes REST endpoints to manage profiles.

## Tech Stack

- **Python 3.11+**
- **FastAPI** — web framework
- **SQLite** — persistence (via stdlib `sqlite3`)
- **httpx** — async HTTP client for external APIs
- **uuid6** — UUID v7 generation
- **uvicorn** — ASGI server

## Setup

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/profiles` | Create a profile (idempotent by name) |
| GET | `/api/profiles` | List all profiles (filterable) |
| GET | `/api/profiles/{id}` | Get a single profile |
| DELETE | `/api/profiles/{id}` | Delete a profile |

### Filters for GET /api/profiles

- `?gender=male`
- `?country_id=NG`
- `?age_group=adult`
- Combinable, case-insensitive

## External APIs Used

- **Genderize** — https://api.genderize.io
- **Agify** — https://api.agify.io
- **Nationalize** — https://api.nationalize.io

## Classification Rules

| Age Range | Group |
|-----------|-------|
| 0–12 | child |
| 13–19 | teenager |
| 20–59 | adult |
| 60+ | senior |

## Testing

```bash
# Create a profile
curl -X POST http://localhost:8000/api/profiles -H "Content-Type: application/json" \
-d '{"name": "ella"}'

# List all profiles
curl http://localhost:8000/api/profiles

# Filter by gender
curl "http://localhost:8000/api/profiles?gender=female"

# Get single profile (replace with actual ID)
curl http://localhost:8000/api/profiles/{id}

# Delete profile
curl -X DELETE http://localhost:8000/api/profiles/{id}
```

## Error Handling

All errors follow this format:
```json
{ "status": "error", "message": "<error message>" }
```

- **400 Bad Request** — Missing or empty name
- **404 Not Found** — Profile ID not found
- **422 Unprocessable Entity** — Invalid request body
- **502 Bad Gateway** — External API failure (Genderize, Agify, or Nationalize)

Nationality = country with highest probability from Nationalize response.

## Error Responses

All errors return:
```json
{ "status": "error", "message": "<description>" }
```

- `400` — Missing or empty name
- `422` — Invalid type (name is not a string)
- `404` — Profile not found
- `502` — External API returned invalid/null data

## Deployment