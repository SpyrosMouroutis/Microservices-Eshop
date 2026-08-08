import os
import json
import threading
import time
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, Header, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import pika
from jose import jwt
import requests

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@product-db:5432/products_db")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class ProductModel(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String, nullable=False)
    img = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    user_username = Column(String, nullable=False)

Base.metadata.create_all(bind=engine)

class ProductCreate(BaseModel):
    title: str
    img: str
    price: float
    quantity: int

class ProductUpdate(BaseModel):
    price: Optional[float] = None
    quantity: Optional[int] = None

class ProductResponse(BaseModel):
    id: int
    title: str
    img: str
    price: float
    quantity: int
    user_username: str

    class Config:
        from_attributes = True

class UserRegister(BaseModel):
    username: str
    email: str
    password: str
    role: str

app = FastAPI(title="Product Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token header")
    token = authorization.split(" ")[1]
    try:
        payload = jwt.get_unverified_claims(token)
        username = payload.get("preferred_username")
        roles = payload.get("realm_access", {}).get("roles", [])
        return {"username": username, "roles": roles}
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

@app.post("/register", status_code=201)
def register_user(user: UserRegister):
    keycloak_admin_url = os.getenv("KEYCLOAK_ADMIN_URL", "http://keycloak:8080")
    
    token_url = f"{keycloak_admin_url}/realms/master/protocol/openid-connect/token"
    token_data = {
        "client_id": "admin-cli",
        "username": "admin",
        "password": "admin",
        "grant_type": "password"
    }
    
    token_res = requests.post(token_url, data=token_data)
    if token_res.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to obtain Keycloak admin token")
    
    admin_token = token_res.json().get("access_token")
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json"
    }

    create_user_url = f"{keycloak_admin_url}/admin/realms/eshop-realm/users"
    user_payload = {
        "username": user.username,
        "email": user.email,
        "enabled": True,
        "credentials": [{"type": "password", "value": user.password, "temporary": False}]
    }
    
    create_res = requests.post(create_user_url, json=user_payload, headers=headers)
    if create_res.status_code not in [201, 200]:
        raise HTTPException(status_code=create_res.status_code, detail="User already exists or registration failed")

    get_user_url = f"{keycloak_admin_url}/admin/realms/eshop-realm/users?username={user.username}"
    users = requests.get(get_user_url, headers=headers).json()
    if not users:
        raise HTTPException(status_code=500, detail="User lookup failed after creation")
    user_id = users[0]["id"]

    get_role_url = f"{keycloak_admin_url}/admin/realms/eshop-realm/roles/{user.role}"
    role_res = requests.get(get_role_url, headers=headers)
    if role_res.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Role '{user.role}' does not exist in realm")
    role_data = role_res.json()

    assign_role_url = f"{keycloak_admin_url}/admin/realms/eshop-realm/users/{user_id}/role-mappings/realm"
    requests.post(assign_role_url, json=[role_data], headers=headers)

    return {"message": "User registered successfully"}

@app.post("/products", response_model=ProductResponse, status_code=201)
def create_product(product: ProductCreate, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    if "seller" not in user["roles"]:
        raise HTTPException(status_code=403, detail="Only sellers can create products")
    db_product = ProductModel(
        title=product.title,
        img=product.img,
        price=product.price,
        quantity=product.quantity,
        user_username=user["username"]
    )
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return db_product

@app.get("/products", response_model=List[ProductResponse])
def get_products(db: Session = Depends(get_db)):
    return db.query(ProductModel).all()

@app.get("/products/search", response_model=List[ProductResponse])
def search_products(name: Optional[str] = None, username: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(ProductModel)
    if name:
        query = query.filter(ProductModel.title.ilike(f"%{name}%"))
    if username:
        query = query.filter(ProductModel.user_username == username)
    return query.all()

@app.get("/products/{product_id}", response_model=ProductResponse)
def get_product(product_id: int, db: Session = Depends(get_db)):
    prod = db.query(ProductModel).filter(ProductModel.id == product_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found")
    return prod

@app.put("/product/{product_id}", response_model=ProductResponse)
def update_product(product_id: int, product_update: ProductUpdate, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    if "seller" not in user["roles"]:
        raise HTTPException(status_code=403, detail="Only sellers can update products")
    prod = db.query(ProductModel).filter(ProductModel.id == product_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found")
    if prod.user_username != user["username"]:
        raise HTTPException(status_code=403, detail="You can only update your own products")
    
    if product_update.price is not None:
        prod.price = product_update.price
    if product_update.quantity is not None:
        prod.quantity = product_update.quantity

    db.commit()
    db.refresh(prod)
    return prod

@app.delete("/product/{product_id}", status_code=200)
def delete_product(product_id: int, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    if "seller" not in user["roles"]:
        raise HTTPException(status_code=403, detail="Only sellers can delete products")
    prod = db.query(ProductModel).filter(ProductModel.id == product_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found")
    if prod.user_username != user["username"]:
        raise HTTPException(status_code=403, detail="You can only delete your own products")
    
    db.delete(prod)
    db.commit()
    return {"message": "Product deleted successfully"}

def start_rabbitmq_consumer():
    rabbitmq_host = os.getenv("RABBITMQ_HOST", "rabbitmq")
    while True:
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(host=rabbitmq_host))
            channel = connection.channel()
            channel.queue_declare(queue="orders_queue")
            channel.queue_declare(queue="order_status_queue")

            def callback(ch, method, properties, body):
                order_data = json.loads(body)
                order_id = order_data.get("id")
                products_list = order_data.get("products", [])

                db = SessionLocal()
                status = "Success"
                
                for item in products_list:
                    p_id = item.get("product_id")
                    amount = item.get("amount")
                    prod = db.query(ProductModel).filter(ProductModel.id == p_id).first()
                    if not prod or prod.quantity < amount:
                        status = "Reject"
                        break

                if status == "Success":
                    for item in products_list:
                        p_id = item.get("product_id")
                        amount = item.get("amount")
                        prod = db.query(ProductModel).filter(ProductModel.id == p_id).first()
                        prod.quantity -= amount
                    db.commit()
                
                db.close()

                response_msg = {"id": order_id, "status": status}
                channel.basic_publish(
                    exchange="",
                    routing_key="order_status_queue",
                    body=json.dumps(response_msg)
                )
                ch.basic_ack(delivery_tag=method.delivery_tag)

            channel.basic_consume(queue="orders_queue", on_message_callback=callback)
            channel.start_consuming()
        except Exception:
            time.sleep(5)

threading.Thread(target=start_rabbitmq_consumer, daemon=True).start()