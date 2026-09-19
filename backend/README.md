# FastAPI Parking Simulator Backend

This backend application integrates with **ParkingSimulator** to automate parking management, including entrance gate opening, spot assignment (range `S1`..`S30`), EV charging fee calculations, payment processing, exit gate opening, and non-blocking auto-gate closure.

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- Python 3.10+ installed on your system.
- `ParkingSimulator-win-x64` executable running locally.

---

### 2. Virtual Environment Setup

Navigate to the `backend` directory:
```powershell
cd backend
```

Create and activate a virtual environment:

**Windows (PowerShell):**
```powershell
python -m venv fastapi-env
.\fastapi-env\Scripts\Activate.ps1
```

**Linux / macOS:**
```bash
python3 -m venv fastapi-env
source fastapi-env/bin/activate
```

---

### 3. Install Dependencies

Install all required packages:
```powershell
pip install -r requirements.txt
```

---

### 4. Configure Parking Simulator (`settings.json`)

Ensure `ParkingSimulator-win-x64/settings/settings.json` has `WebhookUrl` set to your FastAPI server:

```json
{
  "TeamName": "git init",
  "ListenAddress": "http://0.0.0.0:9898",
  "WebhookUrl": "http://127.0.0.1:8000/webhook",
  "ParkingSpeedMuliplier": 1,
  "MinParkingTime": 1,
  "MaxParkingTime": 5,
  "Name": "admin",
  "Password": "admin",
  "GameSpeedMultiplier": 1.0,
  "lvl2Password": "",
  "lvl3Password": ""
}
```

> **Note:** Ensure invalid `//` comments are removed from `settings.json` so the simulator can parse it.

---

### 5. Run the Backend Server

Navigate into the `fastapi_project` directory and start Uvicorn:

```powershell
cd fastapi_project
uvicorn main:app --reload
```

The server will start at: **`http://127.0.0.1:8000`**

---

## 📡 API Endpoints Overview

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| **`http://127.0.0.1:8000/docs`** | `GET` | Interactive Swagger API documentation |
| **`/webhook`** | `POST` | Webhook listener receiving live events from the simulator |
| **`/recent-arrivals`** | `GET` | View recently arrived cars and entry details |
| **`/all-events`** | `GET` | View raw webhook logs |
| **`/list-parking-spots`** | `GET` | Query available parking spots from the simulator |
| **`/barrier-gates/{name}/open`** | `POST` | Manually open a barrier gate |
| **`/barrier-gates/{name}/close`**| `POST` | Manually close a barrier gate |
| **`/car/{name}/goto/{destination}`** | `POST` | Direct a car to a parking spot or exit |
| **`/car/{name}/charge`** | `POST` | Request payment for parking and EV charging |

---

## ✨ System Features & Architecture

1. **Parallel Dual-Queue Architecture (`entry_queue` & `exit_queue`)**:
   - Entrance and Exit event streams run concurrently on separate background workers.
   - Exiting cars never wait behind entering cars.

2. **Thread-Safe Spot Allocation**:
   - Randomly selects available spots from `S1` through `S30`.
   - Uses `asyncio.Lock()` to prevent race conditions when cars enter simultaneously.

3. **EV Charging & Payment Rules**:
   - `parkingCost` = total minutes parked.
   - `chargingCost` = `parkingCost * 2` if the vehicle is an EV/Electric car.
   - Single-charge enforcement prevents double-charging penalties.

4. **Automated Non-Blocking Gate Closure**:
   - Automatically closes barrier gates 5 seconds after opening to allow cars to pass safely.
