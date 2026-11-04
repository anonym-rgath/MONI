import uvicorn

from moni.config import Settings

if __name__ == "__main__":
    uvicorn.run("moni.app:create_app", factory=True, host="0.0.0.0", port=Settings.from_env().port)
