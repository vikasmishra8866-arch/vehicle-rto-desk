import asyncio
from typing import Any, Dict
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Parivahan Service RC Portal")

# Set up Jinja2 templates folder
templates = Jinja2Templates(directory="templates")

# API Configuration
API1_BASE_URL = "https://unsalubriously-unfragrant-rosetta.ngrok-free.dev/api/vehicle-details-only"
API2_BASE_URL = "https://cjpen.vercel.app/vehicle"


def get_valid_value(*values: Any) -> str:
    """Helper to pick the first valid non-null string from API fields."""
    for v in values:
        if v is not None and str(v).strip().upper() not in [
            "NONE",
            "NULL",
            "NA",
            "N/A",
            "",
            "FALSE",
        ]:
            return str(v).strip()
    return "N/A"


async def fetch_api_1(client: httpx.AsyncClient, vehicle_no: str) -> Dict[str, Any]:
    """Fetch vehicle data from API 1 asynchronously."""
    try:
        url = f"{API1_BASE_URL}?regn_no={vehicle_no}"
        headers = {"ngrok-skip-browser-warning": "true"}
        response = await client.get(url, headers=headers, timeout=10.0)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Error fetching API 1: {e}")
    return {}


async def fetch_api_2(client: httpx.AsyncClient, vehicle_no: str) -> Dict[str, Any]:
    """Fetch vehicle data from API 2 asynchronously."""
    try:
        url = f"{API2_BASE_URL}/{vehicle_no}"
        response = await client.get(url, timeout=10.0)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Error fetching API 2: {e}")
    return {}


def consolidate_vehicle_data(api1_data: Dict[str, Any], api2_data: Dict[str, Any], vehicle_no: str) -> Dict[str, Any]:
    """Consolidates parallel API responses into 1 clean Master JSON."""
    a1_result = api1_data.get("meta_data", {}).get("signzy_response", {}).get("result", {})
    a1_cust = api1_data.get("customer_details", {})
    a1_veh = api1_data.get("vehicle_details", {})
    a2_data = api2_data.get("data", {})

    # Model & Variant Merging
    model = get_valid_value(a1_result.get("model"), a2_data.get("vehicle"))
    variant = get_valid_value(a2_data.get("variant"))
    if variant != "N/A" and variant not in model:
        full_model = f"{model} ({variant})"
    else:
        full_model = model

    # Select detailed address
    addr1 = a1_cust.get("communication_address", {}).get("address_line", "")
    addr2 = a2_data.get("presentAddress", "")
    address = addr1 if len(str(addr1)) >= len(str(addr2)) else addr2

    # Consolidate status
    raw_status = get_valid_value(a1_result.get("status"))
    if raw_status == "N/A":
        rc_status = "ACTIVE" if a2_data.get("dataStatus") == 1 else "ACTIVE"
    else:
        rc_status = raw_status

    return {
        "status": "success",
        "searched_vehicle_no": vehicle_no.upper(),
        "vehicle_details": {
            "registration_number": get_valid_value(a1_result.get("regNo"), a2_data.get("regNo"), vehicle_no.upper()),
            "owner_name": get_valid_value(a1_cust.get("full_name"), a2_data.get("owner")),
            "father_name": get_valid_value(a1_result.get("ownerFatherName"), a2_data.get("ownerFatherName")),
            "vehicle_model": full_model,
            "manufacturer": get_valid_value(a1_result.get("vehicleManufacturerName"), a2_data.get("manufacturer")),
            "registration_date": get_valid_value(a1_veh.get("registration_date"), a1_result.get("regDate"), a2_data.get("regDate")),
            "rc_expiry_date": get_valid_value(a1_result.get("rcExpiryDate")),
            "rc_status": rc_status,
            "chassis_number": get_valid_value(api1_data.get("chassis_number"), a2_data.get("chassis")),
            "engine_number": get_valid_value(api1_data.get("engine_number"), a2_data.get("engine")),
            "fuel_type": get_valid_value(a1_result.get("type"), a2_data.get("fuelType")),
            "vehicle_class": get_valid_value(a1_result.get("class"), a2_data.get("vehicleClass")),
            "rto_authority": get_valid_value(a1_result.get("regAuthority"), a2_data.get("regAuthority")),
            "registered_address": get_valid_value(address),
        },
        "compliance_details": {
            "insurance_company": get_valid_value(a1_result.get("vehicleInsuranceCompanyName"), a2_data.get("insuranceCompanyName")),
            "insurance_policy_no": get_valid_value(a1_result.get("vehicleInsurancePolicyNumber"), a2_data.get("insurancePolicyNumber")),
            "insurance_expiry": get_valid_value(api1_data.get("previous_policy_exp_date"), a2_data.get("insuranceUpto")),
            "puc_number": get_valid_value(a1_result.get("puccNumber"), a2_data.get("puccNumber")),
            "puc_expiry": get_valid_value(a1_result.get("puccUpto"), a2_data.get("puccValidUpto")),
            "financer": get_valid_value(a1_result.get("rcFinancer"), a2_data.get("financerName")),
        },
    }


# HTML Interface Route
@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "vehicle_data": None})


# Async Vehicle Lookup Route
@app.get("/search", response_class=HTMLResponse)
async def search_vehicle(request: Request, vehicle_no: str):
    cleaned_vno = vehicle_no.replace(" ", "").replace("-", "").upper()

    # Parallel HTTP Calls using asyncio.gather
    async with httpx.AsyncClient() as client:
        api1_resp, api2_resp = await asyncio.gather(
            fetch_api_1(client, cleaned_vno),
            fetch_api_2(client, cleaned_vno)
        )

    # Consolidate into Master JSON
    master_data = consolidate_vehicle_data(api1_resp, api2_resp, cleaned_vno)

    return templates.TemplateResponse("index.html", {"request": request, "vehicle_data": master_data})


# Pure JSON Endpoint (API Service)
@app.get("/api/v1/vehicle/{vehicle_no}")
async def get_vehicle_json(vehicle_no: str):
    cleaned_vno = vehicle_no.replace(" ", "").replace("-", "").upper()
    async with httpx.AsyncClient() as client:
        api1_resp, api2_resp = await asyncio.gather(
            fetch_api_1(client, cleaned_vno),
            fetch_api_2(client, cleaned_vno)
        )
    return consolidate_vehicle_data(api1_resp, api2_resp, cleaned_vno)
