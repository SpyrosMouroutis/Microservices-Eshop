import os
import json
import threading
import time
from typing import List, Optional, Any
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import pika
from jose import jwt

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@order-db:5432/orders_db")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class OrderModel(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    products = Column(JSON, nullable=False)
    total_price = Column(Float, nullable=False)
    status = Column(String, default="Pending")
    user_username = Column(String, nullable=False)

Base.metadata.create_all(bind=engine)

class OrderItem(BaseModel):
    product_id: int
    title: str
    amount: int
    price: float

class OrderCreate(BaseModel):
    products: List[OrderItem]

class OrderResponse(BaseModel):
    id: int
    products: Any
    total_price: float
    status: str
    user_username: str

    class Config:
        from_attributes = True

app = FastAPI(title="Order Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

def send_to_queue(queue_name: str, message: dict):
    rabbitmq_host = os.getenv("RABBITMQ_HOST", "rabbitmq")
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=rabbitmq_host))
    channel = connection.channel()
    channel.queue_declare(queue=queue_name)
    channel.basic_publish(
        exchange="",
        routing_key=queue_name,
        body=json.dumps(message)
    )
    connection.close()

@app.post("/orders", response_model=OrderResponse, status_code=201)
def create_order(order: OrderCreate, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    if "customer" not in user["roles"]:
        raise HTTPException(status_code=403, detail="Only customers can place orders")
    
    if not order.products:
        raise HTTPException(status_code=400, detail="Cart cannot be empty")

    total_price = sum(item.price * item.amount for item in order.products)
    products_json = [item.dict() for item in order.products]

    db_order = OrderModel(
        products=products_json,
        total_price=total_price,
        status="Pending",
        user_username=user["username"]
    )
    db.add(db_order)
    db.commit()
    db.refresh(db_order)

    event_payload = {
        "id": db_order.id,
        "products": [{"product_id": item.product_id, "amount": item.amount} for item in order.products]
    }
    
    try:
        send_to_queue("orders_queue", event_payload)
    except Exception as e:
        print(f"Failed to publish to broker: {e}")

    return db_order

@app.get("/orders/{username}", response_model=List[OrderResponse])
def get_user_orders(username: str, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    if user["username"] != username and "admin" not in user["roles"]:
        raise HTTPException(status_code=403, detail="Unauthorized access to user orders")
    
    return db.query(OrderModel).filter(OrderModel.user_username == username).all()

def start_status_consumer():
    rabbitmq_host = os.getenv("RABBITMQ_HOST", "rabbitmq")
    while True:
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(host=rabbitmq_host))
            channel = connection.channel()
            channel.queue_declare(queue="order_status_queue")

            def callback(ch, method, properties, body):
                data = json.loads(body)
                order_id = data.get("id")
                new_status = data.get("status")

                db = SessionLocal()
                order = db.query(OrderModel).filter(OrderModel.id == order_id).first()
                if order:
                    order.status = new_status
                    db.commit()
                db.close()
                ch.basic_ack(delivery_tag=method.delivery_tag)

            channel.basic_consume(queue="order_status_queue", on_message_callback=callback)
            channel.start_consuming()
        except Exception:
            time.sleep(5)

threading.Thread(target=start_status_consumer, daemon=True).start()