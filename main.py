from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Pecunia API", version="1.0.0")

# Libera o acesso para o Next.js na Vercel e ambiente local
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Pecunia API is running"}

@app.get("/health")
def health_check():
    return {"status": "ok"}