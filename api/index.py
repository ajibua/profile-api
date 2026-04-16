import sqlite3
import httpx
import asyncio
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from uuid6 import uuid7
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.requests import Request


# Use /tmp on Vercel, otherwise use local path
DB_PATH = os.path.join(tempfile.gettempdir(), "profiles.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id   TEXT PRIMARY KEY,
            name  TEXT UNIQUE NOT NULL,
            gender TEXT,
            gender_probability REAL,
            sample_size INTEGER,
            age INTEGER,
            age_group TEXT,
            country_id TEXT,
            country_probability REAL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    
app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"], 
)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "message": exc.detail}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"status": "error", "message": "Invalid request body"},
    )

def classify_age(age: int) -> str:
    if age <= 12:
        return "child"
    elif age <= 19:
        return "teenager"
    elif age <= 59:
        return "adult"
    else:
        return "senior"
    
def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)

async def fetch_external_apis(name: str) -> dict:
    """Call all three external APIs concurrently and return parsed results."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                gender_resp = await client.get(f"https://api.genderize.io?name={name}")
                gender_resp.raise_for_status()
            except Exception as e:
                raise HTTPException(status_code=502, detail="Genderize returned an invalid response")
            
            try:
                age_resp = await client.get(f"https://api.agify.io?name={name}")
                age_resp.raise_for_status()
            except Exception as e:
                raise HTTPException(status_code=502, detail="Agify returned an invalid response")
            
            try:
                nation_resp = await client.get(f"https://api.nationalize.io?name={name}")
                nation_resp.raise_for_status()
            except Exception as e:
                raise HTTPException(status_code=502, detail="Nationalize returned an invalid response")
        
        gender_data = gender_resp.json()
        age_data = age_resp.json()
        nation_data = nation_resp.json()
        
        if not gender_data.get("gender") or gender_data.get("count", 0) == 0:
            raise HTTPException(status_code=502, detail="Genderize returned an invalid response")
        
        if age_data.get("age") is None: 
            raise HTTPException(status_code=502, detail="Agify returned an invalid response")
        
        countries = nation_data.get("country") or []
        if not countries:
            raise HTTPException(status_code=502, detail="Nationalize returned an invalid response")
        
        top_country = max(countries, key=lambda c: c["probability"])
        
        return {
            "gender": gender_data["gender"],
            "gender_probability": gender_data["probability"],
            "sample_size": gender_data["count"],
            "age": age_data["age"],
            "age_group": classify_age(age_data["age"]),
            "country_id": top_country["country_id"],
            "country_probability": top_country["probability"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail="Error fetching external API data")
        
class CreateProfileRequest(BaseModel):
    name: str   
    
@app.post("/api/profiles", status_code=201)
async def create_profile(body: CreateProfileRequest):
    if not body.name or body.name.strip() == "":
        raise HTTPException(status_code=400, detail="Missing or empty name")
    
    name = body.name.strip().lower()
    
    db = get_db()
    try:
        existing = db.execute(
            "SELECT * FROM profiles WHERE name = ?", (name,)
        ).fetchone()
        
        if existing:
            return {
                "status": "success",
                "message": "Profile already exists",
                "data": row_to_dict(existing),
            }
            
        api_data = await fetch_external_apis(name)
        profile = {
            "id": str(uuid7()),
            "name": name,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **api_data,
        }
        
        try:
            db.execute(
                """
                   INSERT INTO profiles
                   (id, name, gender, gender_probability, sample_size, age, age_group, country_id, country_probability, created_at)
                   VALUES 
                   (:id, :name, :gender, :gender_probability, :sample_size, :age, :age_group, :country_id, :country_probability, :created_at)
                """,
                profile,
            )
            db.commit()
            return {"status": "success", "data": profile}
        except sqlite3.IntegrityError:
            db.rollback()
            raise HTTPException(status_code=400, detail="Profile with this name already exists")
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating profile: {str(e)}")
    finally:
        db.close()
        
@app.get("/api/profiles/{profile_id}")
async def get_profile(profile_id: str):
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Profile not found")
        return {"status": "success", "data": row_to_dict(row)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        db.close()
        
@app.get("/api/profiles")
async def list_profiles(
    gender: Optional[str] = Query(default=None),
    country_id: Optional[str] = Query(default=None),
    age_group: Optional[str] = Query(default=None),
):
    db = get_db()
    try:
        query = "SELECT * FROM profiles WHERE 1=1"
        params = []
        
        if gender is not None:
            query += " AND LOWER(gender) = LOWER(?)"
            params.append(gender)
        if country_id is not None:
            query += " AND LOWER(country_id) = LOWER(?)"
            params.append(country_id)
        if age_group is not None:
            query += " AND LOWER(age_group) = LOWER(?)"
            params.append(age_group)
            
        rows = db.execute(query, params).fetchall()
        
        data = [
            {
                "id": r["id"],
                "name": r["name"],
                "gender": r["gender"],
                "age": r["age"],
                "age_group": r["age_group"],
                "country_id": r["country_id"],   
            }
            for r in rows
        ]
        
        return {"status": "success", "count": len(data), "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        db.close()

@app.delete("/api/profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: str):
    db = get_db()
    try:
        existing = db.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        
        if not existing:
            raise HTTPException(status_code=404, detail="Profile not found")
        
        db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        db.commit()
        return None
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        db.close()
