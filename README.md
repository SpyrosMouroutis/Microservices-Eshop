# Cloud E-Shop: Event-Driven Microservices Platform

A cloud-native, distributed e-commerce platform designed around **microservices architecture**, **asynchronous event-driven communication**, and **centralized Identity & Access Management (IAM)**. Containerized with Docker Compose and deployed on Google Cloud Platform (GCP).

> **Academic Context:** Developed for the *Cloud & Fog Computing Services (ΠΛΗ513)* graduate/undergraduate course at the **Technical University of Crete (TUC)**.

---

## Key Architectural Highlights

* **Database-per-Service Pattern:** Strict data isolation between `Product Service` (`products_db`) and `Order Service` (`orders_db`) using separate PostgreSQL instances with persistent Docker volumes.
* **Choreography-Based Saga (Eventual Consistency):** Decouples order creation from inventory deduction via **RabbitMQ** message queues (`orders_queue` & `order_status_queue`), ensuring fault-tolerant, atomic stock validation without synchronous HTTP coupling.
* **Centralized OAuth2 / OIDC Security:** Integrates **Keycloak** (`eshop-realm`) for stateless JWT authentication, user registration, and Role-Based Access Control (`customer` vs. `seller` roles) enforced at both the UI and API gateway/service layers.
* **Cloud Deployment Ready:** Orchestrated via a single `docker-compose.yml` for reproducible local development and production deployment on a **Google Cloud Platform (GCP) Virtual Machine**.

---

## System Architecture

```text
                        +-----------------------------------+
                        |       Frontend SPA (Client)       |
                        |  HTML5 / CSS3 / Vanilla JS / JWT  |
                        +-----------------------------------+
                           |              |               |
         OAuth2 / OIDC     |              | REST (:5002)  | REST (:5001)
         (Auth & RBAC)     |              |               |
                           v              v               v
                +---------------+   +-----------+   +-------------+
                |   Keycloak    |   |   Order   |   |   Product   |
                |  (:8080 IAM)  |   |  Service  |   |   Service   |
                +---------------+   +-----------+   +-------------+
                                      |   ^   |       |   ^   |
                                      |   |   |       |   |   |
                        SQLAlchemy    |   |   |       |   |   |  SQLAlchemy
                        +-------------+   |   |       |   |   +-------------+
                        v                 |   |       |   |                 v
                +---------------+         |   |       |   |         +---------------+
                |   order-db    |         |   |       |   |         |  product-db   |
                | (PostgreSQL)  |         |   |       |   |         | (PostgreSQL)  |
                +---------------+         |   |       |   |         +---------------+
                                          |   |       |   |
                       order_status_queue |   |       |   | orders_queue
                                          |   v       v   |
                                    +---------------------------+
                                    |      RabbitMQ Broker      |
                                    |   (Async Pub/Sub Queues)  |
                                    +---------------------------+
```

### Asynchronous Order & Inventory Workflow

To prevent race conditions and tight coupling between services, checkout operations execute asynchronously:

1. **Order Placement (`Order Service`):** An authenticated `customer` submits their cart (`POST /orders`). The order is persisted in `orders_db` with `status = "Pending"` and an event payload (`{id, products}`) is published to `orders_queue`.
2. **Stock Validation (`Product Service`):** A background daemon worker consumes from `orders_queue` and verifies available stock in `products_db` for every line item.
3. **Commit or Compensate:**
   * **Sufficient Stock:** Quantities are decremented atomically, committed to PostgreSQL, and `{id, status: "Success"}` is published to `order_status_queue`.
   * **Insufficient Stock:** No inventory changes are committed, and `{id, status: "Reject"}` is published to `order_status_queue`.
4. **Order Finalization (`Order Service`):** A background consumer listens to `order_status_queue` and updates the order's final status in `orders_db`.

---

## Microservices & Features

### 1. Frontend Application (`index.html`)
* **Dynamic Role-Based UI:** Automatically adapts navigation and views upon JWT decoding (`customer` sees *Products*, *Cart*, *Orders*; `seller` sees *My Products*).
* **Client-Side State:** Persists active JWT tokens, user metadata, and shopping cart items in `localStorage`.
* **Live Search & Cart Management:** Real-time product filtering, quantity aggregation, and order status tracking (`Pending` / `Success` / `Reject`).

### 2. Product Service (`main.py` — Port `5001`)
* **Inventory CRUD:** Allows `seller` accounts to create, update (price/quantity), and delete their own product listings, enforced via JWT ownership checks.
* **Catalog Discovery:** Public endpoints for listing all products and case-insensitive substring/seller search.
* **Keycloak Admin Integration:** Exposes `/register` to programmatically provision users in `eshop-realm` and bind realm roles.
* **Background Consumer:** Resilient RabbitMQ worker with automatic reconnection backoff for processing inventory deductions.

### 3. Order Service (`main_2.py` — Port `5002`)
* **Checkout Pipeline:** Validates `customer` role claims, calculates order totals, persists JSON line-item snapshots, and dispatches broker events.
* **Order History:** Enforces user-level authorization so customers can only query their own order history (`GET /orders/{username}`).

---

## API Endpoints Overview

| Service | Method | Endpoint | Access Role | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Product** | `POST` | `/register` | Public | Registers a new user in Keycloak and assigns `customer` or `seller` role |
| **Product** | `GET` | `/products` | Public | Retrieves the full product catalog |
| **Product** | `GET` | `/products/search` | Public | Filters catalog by `?name=` and/or `?username=` |
| **Product** | `POST` | `/products` | `seller` | Creates a new product listing |
| **Product** | `PUT` | `/product/{id}` | `seller` (Owner) | Updates price and/or stock quantity |
| **Product** | `DELETE` | `/product/{id}` | `seller` (Owner) | Deletes a product listing |
| **Order** | `POST` | `/orders` | `customer` | Submits a new order (`Pending`) and publishes to RabbitMQ |
| **Order** | `GET` | `/orders/{username}` | Owner / `admin` | Fetches order history and real-time fulfillment statuses |

> Interactive OpenAPI / Swagger documentation is automatically generated at `http://localhost:5001/docs` and `http://localhost:5002/docs`.

---

## Getting Started

### Prerequisites
* [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/)

### Quick Start (Local or GCP VM)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/spyrosmouroutis/cloud-eshop-microservices.git
   cd cloud-eshop-microservices
   ```

2. **Launch the entire stack:**
   ```bash
   docker-compose up --build -d
   ```

3. **Verify Keycloak Configuration (`http://localhost:8080`):**
   * Log into the Admin Console (`admin` / `admin`).
   * Ensure realm `eshop-realm` exists with roles `customer` and `seller`, and public client `eshop-app` (with *Direct Access Grants* enabled).

4. **Access the Application:**
   * **Web Client:** `http://localhost` (or open `index.html`)
   * **Product Service API:** `http://localhost:5001/docs`
   * **Order Service API:** `http://localhost:5002/docs`
   * **RabbitMQ Management UI:** `http://localhost:15672`

---

## Tech Stack

* **Backend:** Python 3, FastAPI, SQLAlchemy, Pydantic, Pika (AMQP), Python-JOSE (JWT)
* **Frontend:** HTML5, CSS3, Vanilla JavaScript (ES6+ Fetch API)
* **Data Persistence:** PostgreSQL (Isolated databases + Docker Named Volumes)
* **Message Broker:** RabbitMQ
* **Authentication & IAM:** Keycloak (OAuth 2.0 / OpenID Connect)
* **Infrastructure & DevOps:** Docker, Docker Compose, Google Cloud Platform (Compute Engine VM)

---

## Author

**Spyridon Mouroutis**  
*Electrical & Computer Engineering — Technical University of Crete*