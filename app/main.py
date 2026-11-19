from contextlib import asynccontextmanager
from fastapi import FastAPI

from .db import init_db
from .routes import addresses, admin, auth, cart, dev, orders, products, shops, wallets


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Store Backend — SQLite + Raw SQL", version="0.1.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(shops.router)
app.include_router(cart.router)
app.include_router(addresses.router)
app.include_router(wallets.router)
app.include_router(orders.router)
app.include_router(dev.router)
app.include_router(admin.router)


@app.get("/health", tags=["System"])
def health():
    return {"status": "ok"}
