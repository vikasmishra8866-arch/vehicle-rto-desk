import os
import re
import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

load_dotenv()

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("RTOAggregator")

# FastAPI App & Rate Limiter Setup
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Vehicle RTO Data Aggregator", version="1.0.0")
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return _rate_limit_exceeded_handler(request, exc)

templates = Jinja2Templates(directory="templates")

# Custom Jinja Filter for Expiry Status Calculation
def compute_expiry_status(date_str: Any) -> str:
    """Returns 'ACTIVE', 'EXPIRED', or 'N/A' depending on date comparison against system date."""
    if not date_str or str(date_str).strip().upper() in ["N/A", "NONE", "NULL", ""]:
        return "N/A"
    
    clean_str = str(date_str).strip()
    parsed_date = None
    
    # Common date formats seen in RTO APIs
    formats = [
        "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d",
        "%d-%b-%Y", "%d %b %Y", "%b-%Y", "%m/%Y", "%Y"
    ]
    
    for fmt in formats:
        try:
            parsed_date = datetime.strptime(clean_str, fmt)
            break
        except ValueError:
            continue

    if not parsed_date:
        # Check if year string is embedded
        match = re.search(r'\b(20\d{2})\b', clean_str)
        if match:
            year = int(match.group(1))
            now = datetime.now()
            return "ACTIVE" if year >= now.year else "EXPIRED"
        return "N/A"

    today = datetime.now()
    if fmt in ["%b-%Y", "%m/%Y", "%Y"]:
        # Month/Year fallback comparison
        if parsed_date.year > today.year or (parsed_date.year == today.year and parsed_date.month >= today.month):
            return "ACTIVE"
        return "EXPIRED"

    return "ACTIVE" if parsed_date.date() >= today.date() else "EXPIRED"

templates.env.filters["expiry_status"] = compute_expiry_status

# Environment Config
API_URLS = [
    os.getenv("API_URL_1", "https://unsalubriously-unfragrant-rosetta.ngrok-free.dev/api/vehicle-details-only?regn_no={VEHICLE_NO}"),
    os.getenv("API_URL_2", "https://randkikichut.vercel.app/?vehicle_number={VEHICLE_NO}"),
    os.getenv("API_URL_3", "https://cjpen.vercel.app/vehicle/{VEHICLE_NO}"),
    os.getenv("API_URL_4", "https://cjpen.vercel.app/vehicle/{VEHICLE_NO}")
]

ALLOW_INSECURE_SSL = os.getenv("ALLOW_INSECURE_SSL", "False").lower() in ("true", "1", "yes")

# Field Canonical Mapping Dictionary
FIELD_MAPPING = {
    # Identity
    "regn_no": ["regn_no", "registration_number", "vehicle_number", "reg_no", "rc_number"],
    "maker": ["maker", "maker_name", "manufacturer", "maker_desc"],
    "model": ["model", "maker_model", "model_name"],
    "variant": ["variant", "vehicle_variant", "variant_name"],
    "fuel_type": ["fuel", "fuel_type", "fuel_desc"],
    "norms": ["norms", "norms_type", "emission_norms", "vehicle_norms"],
    
    # Owner
    "owner_name": ["owner_name", "owner", "registered_owner_name", "owner_name_vahan", "current_owner_name"],
    "father_name": ["father_name", "father_husband_name", "f_name", "care_of", "fathername"],
    "owner_serial": ["owner_serial", "owner_sr_no", "owner_count", "owner_number"],
    "nominee": ["nominee", "nominee_name"],
    
    # Address
    "addresses": ["present_address", "permanent_address", "address", "owner_address", "full_address", "current_address"],
    
    # Technical Specs
    "chassis_no": ["chassis_no", "chassis_number", "chassis"],
    "engine_no": ["engine_no", "engine_number", "engine"],
    "engine_cc": ["cubic_capacity", "cc", "engine_capacity", "cubic_cap"],
    "unladen_weight": ["unladen_weight", "unladen_wt", "weight"],
    "color": ["color", "vehicle_color", "colour"],
    "vehicle_class": ["vehicle_class", "vh_class", "class_desc"],
    
    # Dates & RTO Compliance
    "rto_name": ["rto", "rto_name", "registering_authority", "state_rto"],
    "reg_date": ["regn_dt", "registration_date", "reg_date", "rc_regn_dt"],
    "mfg_date": ["mfg_date", "mfg_dt", "manufacturing_date", "manuf_date", "mfg_yr", "mfg_month_yr"],
    "rc_expiry": ["fit_upto", "rc_expiry", "valid_upto", "rc_valid_upto"],
    "tax_upto": ["tax_upto", "tax_valid_upto", "tax_expiry"],
    
    # Financing, Insurance & PUC
    "financer": ["financer", "financer_name", "hypothecation"],
    "insurance_company": ["insurance_company", "insurance_name", "insurer", "ic_name"],
    "policy_no": ["insurance_policy_no", "policy_no", "insurance_no"],
    "insurance_expiry": ["insurance_upto", "insurance_expiry", "policy_expiry"],
    "puc_number": ["puc_number", "puc_no", "puc_cert_no"],
    "puc_expiry": ["puc_upto", "puc_expiry", "pucc_valid_upto"]
}

# Reverse Mapping Lookup
REVERSE_LOOKUP: Dict[str, str] = {}
for canonical, variants in FIELD_MAPPING.items():
    for variant in variants:
        REVERSE_LOOKUP[variant.lower()] = canonical


def normalize_key(key: str) -> str:
    """Strip spaces/dashes and convert to lowercase."""
    return re.sub(r'[\s\-_]+', '', key.lower())


def flatten_dictionary(data: Any, parent_key: str = '', sep: str = '_') -> Dict[str, Any]:
    """Recursively flatten nested API JSON payloads."""
    items: List[tuple] = []
    if isinstance(data, dict):
        for k, v in data.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, (dict, list)):
                items.extend(flatten_dictionary(v, new_key, sep=sep).items())
            else:
                items.append((new_key, v))
    elif isinstance(data, list):
        for i, elem in enumerate(data):
            if isinstance(elem, (dict, list)):
                items.extend(flatten_dictionary(elem, f"{parent_key}_{i}", sep=sep).items())
            else:
                items.append((f"{parent_key}_{i}", elem))
    else:
        items.append((parent_key, data))
    return dict(items)


def is_valid_value(val: Any) -> bool:
    """Return False if value is empty or represents a placeholder."""
    if val is None:
        return False
    s = str(val).strip().lower()
    return s not in ["", "null", "none", "n/a", "undefined", "-", "--"]


async def fetch_single_api(client: httpx.AsyncClient, url_template: str, vehicle_no: str) -> Optional[Dict[str, Any]]:
    """Asynchronously fetch data from a single upstream API endpoint."""
    url = url_template.replace("{VEHICLE_NO}", vehicle_no)
    try:
        response = await client.get(url, timeout=6.0)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and data:
                return data
            elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                return data[0]
    except Exception as exc:
        logger.warning(f"Failed response from API [{url}]: {str(exc)}")
    return None


def merge_and_normalize_rc_data(raw_api_responses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Applies Canonical Field Routing, Longest String Priority Merging,
    Address Pipeline Processing, and Transfer Logic.
    """
    collected: Dict[str, List[str]] = {key: [] for key in FIELD_MAPPING.keys()}
    
    for raw_data in raw_api_responses:
        if not raw_data:
            continue
        flat = flatten_dictionary(raw_data)
        
        for k, v in flat.items():
            if not is_valid_value(v):
                continue
            
            clean_k = normalize_key(k)
            # Find matching canonical field
            mapped_canonical = None
            for variant, canonical in REVERSE_LOOKUP.items():
                if normalize_key(variant) in clean_k:
                    mapped_canonical = canonical
                    break
            
            if mapped_canonical:
                str_val = str(v).strip()
                if str_val not in collected[mapped_canonical]:
                    collected[mapped_canonical].append(str_val)

    # Smart Priority Merge Result Container
    final_data: Dict[str, Any] = {}

    # 1. Standard Fields Priority Selection (Longest, most informative non-empty string)
    for key, val_list in collected.items():
        if key == "addresses":
            continue
        if val_list:
            # Sort by string length descending
            sorted_vals = sorted(val_list, key=lambda x: len(x), reverse=True)
            final_data[key] = sorted_vals[0]
        else:
            final_data[key] = "N/A"

    # 2. Transfer Handling & Owner Serial logic
    if final_data.get("owner_serial") == "N/A":
        if final_data.get("owner_name") != "N/A":
            final_data["owner_serial"] = "1"

    # 3. Address Pipeline (LONGEST VALID STRING RULE)
    address_candidates = collected.get("addresses", [])
    # Filter out redundant substrings
    cleaned_addresses = []
    for addr in sorted(address_candidates, key=lambda x: len(x), reverse=True):
        if not any(addr in existing for existing in cleaned_addresses):
            cleaned_addresses.append(addr)

    if cleaned_addresses:
        final_data["main_address"] = cleaned_addresses[0]
        final_data["all_addresses"] = cleaned_addresses
    else:
        final_data["main_address"] = "N/A"
        final_data["all_addresses"] = []

    # Clean Up Format of Reg No
    if final_data["regn_no"] != "N/A":
        final_data["regn_no"] = final_data["regn_no"].replace(" ", "").upper()

    return final_data


@app.get("/", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def render_dashboard(request: Request):
    """Render initial empty Glassmorphic Dashboard."""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "searched": False,
        "vehicle_data": None,
        "raw_json": None,
        "error_message": None
    })


@app.post("/", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def aggregate_vehicle_rc(request: Request, vehicle_no: str = Form(...)):
    """Accept search request, call all APIs concurrently, aggregate, and render UI."""
    clean_veh_no = re.sub(r'[^a-zA-Z0-9]', '', vehicle_no).upper()
    
    if not clean_veh_no:
        return templates.TemplateResponse("index.html", {
            "request": request,
            "searched": True,
            "vehicle_data": None,
            "raw_json": None,
            "error_message": "Please enter a valid Registration Number."
        })

    # Execute Concurrent Fetching across all 4 endpoints
    async with httpx.AsyncClient(verify=not ALLOW_INSECURE_SSL, timeout=7.0) as client:
        tasks = [fetch_single_api(client, url_template, clean_veh_no) for url_template in API_URLS]
        raw_responses = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out exceptions and empty dicts
    valid_raw_responses = [r for r in raw_responses if isinstance(r, dict) and r]

    if not valid_raw_responses:
        # All APIs failed or returned no usable data
        return templates.TemplateResponse("index.html", {
            "request": request,
            "searched": True,
            "vehicle_data": None,
            "raw_json": None,
            "error_message": "Details Not Found"
        })

    # Process & Aggregate Data
    aggregated_rc = merge_and_normalize_rc_data(valid_raw_responses)
    
    # Override vehicle_no if it was N/A with searched term
    if aggregated_rc["regn_no"] == "N/A":
        aggregated_rc["regn_no"] = clean_veh_no

    return templates.TemplateResponse("index.html", {
        "request": request,
        "searched": True,
        "vehicle_data": aggregated_rc,
        "raw_json": valid_raw_responses,
        "error_message": None,
        "searched_query": clean_veh_no
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
