import os
import re
import asyncio
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

import httpx
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

load_dotenv()

# Initialize Rate Limiter
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Elite Vehicle RTO Engine", version="2.0.0")
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "status": "error",
            "message": "Rate limit exceeded. You can only make 5 requests per minute."
        }
    )

templates = Jinja2Templates(directory="templates")

# Default API URL Endpoints
DEFAULT_URL_1 = "https://unsalubriously-unfragrant-rosetta.ngrok-free.dev/api/vehicle-details-only?regn_no={VEHICLE_NO}"
DEFAULT_URL_2 = "https://randkikichut.vercel.app/?vehicle_number={VEHICLE_NO}"
DEFAULT_URL_3 = "https://cjpen.vercel.app/vehicle/{VEHICLE_NO}"
DEFAULT_URL_4 = "https://cjpen.vercel.app/vehicle/{VEHICLE_NO}"

def get_api_urls(vehicle_no: str) -> List[str]:
    v_no = vehicle_no.upper().strip()
    u1 = os.getenv("API_URL_1", DEFAULT_URL_1)
    u2 = os.getenv("API_URL_2", DEFAULT_URL_2)
    u3 = os.getenv("API_URL_3", DEFAULT_URL_3)
    u4 = os.getenv("API_URL_4", DEFAULT_URL_4)
    
    return [
        u1.format(VEHICLE_NO=v_no),
        u2.format(VEHICLE_NO=v_no),
        u3.format(VEHICLE_NO=v_no),
        u4.format(VEHICLE_NO=v_no),
    ]

# Normalization & Key Extraction Utilities
def normalize_key(key: str) -> str:
    """Strips spaces and special characters to convert keys into clean snake_case format."""
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', str(key))
    s2 = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()
    return re.sub(r'[^a-z0-9_]', '', s2).strip('_')

# Alias Definitions for Mandatory Box Mapping
ALIAS_MAP = {
    "vehicle_number": ["vehicle_number", "regn_no", "registration_no", "rc_regn_no", "reg_no", "vehicleno"],
    "maker": ["maker", "maker_description", "manufacturer", "make", "maker_name"],
    "model": ["model", "maker_model", "model_name", "vehicle_model"],
    "variant": ["variant", "vehicle_variant", "sub_model"],
    "fuel_type": ["fuel_type", "fuel", "fuel_desc", "fuel_type_descr"],
    "emission_norms": ["emission_norms", "norms_type", "norms", "norms_desc"],
    "owner_name": ["owner_name", "owner", "registered_owner_name", "owner_name_vahan", "current_owner"],
    "father_name": ["father_name", "father_husband_name", "f_name", "care_of", "husband_name"],
    "owner_serial": ["owner_serial", "owner_count", "owner_number", "owner_seq", "owner_sr"],
    "nominee": ["nominee", "nominee_name", "nominee_details"],
    "address": ["present_address", "permanent_address", "address", "owner_address", "full_address", "main_address"],
    "chassis_number": ["chassis_number", "chassis_no", "chassis"],
    "engine_number": ["engine_number", "engine_no", "engine"],
    "cubic_capacity": ["cubic_capacity", "engine_cc", "cc", "cubic_cap"],
    "unladen_weight": ["unladen_weight", "weight", "vehicle_weight"],
    "vehicle_color": ["vehicle_color", "color", "colour"],
    "vehicle_class": ["vehicle_class", "class", "vehicle_type", "category"],
    "financer_name": ["financer", "financer_name", "hypothecation_details", "bank_name", "financed_by"],
    "rto_name": ["rto_name", "registering_authority", "rto", "registered_at", "rto_code"],
    "registration_date": ["registration_date", "regn_dt", "reg_date", "rc_regn_dt", "registered_date"],
    "mfg_date": ["mfg_date", "manufacturing_year", "mfg_year", "manu_month_yr", "manufacturing_date"],
    "rc_expiry": ["rc_expiry", "fit_upto", "rc_valid_upto", "rc_expiry_date", "rc_validity"],
    "tax_expiry": ["tax_expiry", "tax_upto", "tax_valid_upto"],
    "puc_number": ["puc_number", "puc_no", "puc_certificate_no", "pucc_no"],
    "puc_expiry": ["puc_expiry", "puc_upto", "puc_valid_upto"],
    "insurance_company": ["insurance_company", "insurance_co", "insurance_comp", "insurance_name", "insurer"],
    "policy_number": ["policy_number", "insurance_policy_no", "policy_no"],
    "policy_expiry": ["policy_expiry", "insurance_upto", "insurance_valid_upto", "insurance_expiry"],
    "prev_ncb": ["prev_ncb", "ncb", "no_claim_bonus"]
}

# Reverse Mapping Set to isolate non-standard keys for Box 9
ALL_MAPPED_NORM_KEYS = set()
for aliases in ALIAS_MAP.values():
    for alias in aliases:
        ALL_MAPPED_NORM_KEYS.add(normalize_key(alias))

def extract_flat_dict(raw_json: Any, parent_key: str = '') -> Dict[str, Any]:
    """Recursively flattens nested dictionaries while preserving path contexts."""
    items: List[tuple] = []
    if isinstance(raw_json, dict):
        for k, v in raw_json.items():
            new_key = f"{parent_key}_{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(extract_flat_dict(v, new_key).items())
            elif isinstance(v, list):
                for idx, item in enumerate(v):
                    if isinstance(item, dict):
                        items.extend(extract_flat_dict(item, f"{new_key}_{idx}").items())
            else:
                items.append((new_key, v))
    return dict(items)

def extract_field_value(flat_responses: List[Dict[str, Any]], field_key: str) -> str:
    """Searches across all flattened responses for the best non-null value matching aliases."""
    aliases = ALIAS_MAP.get(field_key, [])
    candidates = []

    for flat_dict in flat_responses:
        for orig_key, val in flat_dict.items():
            norm_k = normalize_key(orig_key)
            if any(norm_k == normalize_key(alias) or norm_k.endswith(f"_{normalize_key(alias)}") for alias in aliases):
                if val not in [None, "", "N/A", "null", "None", "-"]:
                    candidates.append(str(val).strip())

    if not candidates:
        return "N/A"

    # Deduplication & richness check: select longest non-duplicate string
    candidates = list(dict.fromkeys(candidates))
    candidates.sort(key=len, reverse=True)
    return candidates[0]

def parse_owner_serial(val: Any) -> int:
    if val:
        nums = re.findall(r'\d+', str(val))
        if nums:
            return int(nums[0])
    return 1

async def fetch_api(client: httpx.AsyncClient, url: str) -> Optional[dict]:
    try:
        response = await client.get(url, timeout=8.0)
        if response.status_code == 200:
            res_json = response.json()
            if isinstance(res_json, dict) and res_json:
                return res_json
    except Exception:
        pass
    return None

def merge_rto_data(responses: List[dict], vehicle_no: str) -> Optional[dict]:
    valid_responses = [r for r in responses if r and isinstance(r, dict)]
    if not valid_responses:
        return None

    flat_responses = [extract_flat_dict(resp) for resp in valid_responses]

    # Owner & Address Strategy
    owner_records = []
    for flat_dict in flat_responses:
        owner_ser_val = None
        for k, v in flat_dict.items():
            if normalize_key(k) in [normalize_key(a) for a in ALIAS_MAP["owner_serial"]]:
                owner_ser_val = v
                break
        
        addr_val = None
        for k, v in flat_dict.items():
            if normalize_key(k) in [normalize_key(a) for a in ALIAS_MAP["address"]]:
                if v not in [None, "", "N/A", "null"]:
                    addr_val = str(v).strip()
                    break

        owner_records.append({
            "owner_serial": parse_owner_serial(owner_ser_val),
            "address": addr_val
        })

    max_owner_serial = max([r["owner_serial"] for r in owner_records]) if owner_records else 1
    highest_owner_records = [r for r in owner_records if r["owner_serial"] == max_owner_serial]

    main_address = "N/A"
    longest_len = -1
    for rec in highest_owner_records:
        if rec["address"]:
            if len(rec["address"]) > longest_len:
                longest_len = len(rec["address"])
                main_address = rec["address"]

    all_addresses = []
    for rec in owner_records:
        if rec["address"] and rec["address"] != main_address and rec["address"] not in all_addresses:
            all_addresses.append(rec["address"])

    # Extract Primary Fields
    financer = extract_field_value(flat_responses, "financer_name")
    is_financed = "YES (FINANCED)" if financer != "N/A" and financer != "" else "NO / UNFINANCED"

    # Catch-All Box 9 Processing (Strictly Non-Standard Keys Only)
    additional_specs = {}
    for flat_dict in flat_responses:
        for orig_key, val in flat_dict.items():
            norm_k = normalize_key(orig_key)
            
            # Check if key is already handled in Boxes 1 through 8
            is_mapped = False
            for mapped_key in ALL_MAPPED_NORM_KEYS:
                if norm_k == mapped_key or norm_k.endswith(f"_{mapped_key}"):
                    is_mapped = True
                    break

            if not is_mapped and val not in [None, "", "N/A", "null", "None", "-"]:
                formatted_label = orig_key.replace("_", " ").replace(".", " ").title()
                # Clean up nested path prefixes
                formatted_label = re.sub(r'^\d+\s*', '', formatted_label).strip()
                if formatted_label not in additional_specs:
                    additional_specs[formatted_label] = str(val).strip()

    master_payload = {
        "primary_identity": {
            "vehicle_number": vehicle_no.upper(),
            "maker": extract_field_value(flat_responses, "maker"),
            "model": extract_field_value(flat_responses, "model"),
            "variant": extract_field_value(flat_responses, "variant"),
            "fuel_type": extract_field_value(flat_responses, "fuel_type"),
            "emission_norms": extract_field_value(flat_responses, "emission_norms")
        },
        "owner_details": {
            "owner_name": extract_field_value(flat_responses, "owner_name"),
            "father_husband_name": extract_field_value(flat_responses, "father_name"),
            "owner_serial": str(max_owner_serial),
            "nominee": extract_field_value(flat_responses, "nominee")
        },
        "address_details": {
            "main_address": main_address,
            "all_addresses": all_addresses
        },
        "technical_specs": {
            "chassis_number": extract_field_value(flat_responses, "chassis_number"),
            "engine_number": extract_field_value(flat_responses, "engine_number"),
            "cubic_capacity": extract_field_value(flat_responses, "cubic_capacity"),
            "unladen_weight": extract_field_value(flat_responses, "unladen_weight"),
            "vehicle_color": extract_field_value(flat_responses, "vehicle_color"),
            "vehicle_class": extract_field_value(flat_responses, "vehicle_class")
        },
        "financed_status": {
            "is_financed": is_financed,
            "financer_name": financer
        },
        "rc_compliance": {
            "rto_name": extract_field_value(flat_responses, "rto_name"),
            "registration_date": extract_field_value(flat_responses, "registration_date"),
            "mfg_date": extract_field_value(flat_responses, "mfg_date"),
            "rc_expiry": extract_field_value(flat_responses, "rc_expiry"),
            "tax_expiry": extract_field_value(flat_responses, "tax_expiry")
        },
        "puc_details": {
            "puc_number": extract_field_value(flat_responses, "puc_number"),
            "puc_expiry": extract_field_value(flat_responses, "puc_expiry")
        },
        "insurance_details": {
            "company_name": extract_field_value(flat_responses, "insurance_company"),
            "policy_number": extract_field_value(flat_responses, "policy_number"),
            "policy_expiry": extract_field_value(flat_responses, "policy_expiry"),
            "prev_ncb": extract_field_value(flat_responses, "prev_ncb")
        },
        "additional_specs": additional_specs
    }

    return master_payload

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/aggregate")
@limiter.limit("5/minute")
async def aggregate_vehicle_data(request: Request, vehicle_no: str = Query(..., min_length=4)):
    urls = get_api_urls(vehicle_no)
    allow_insecure = os.getenv("ALLOW_INSECURE_SSL", "false").lower() == "true"
    
    async with httpx.AsyncClient(verify=not allow_insecure) as client:
        tasks = [fetch_api(client, url) for url in urls]
        responses = await asyncio.gather(*tasks)
        
    merged_data = merge_rto_data(responses, vehicle_no)
    
    if not merged_data:
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "message": "Details Not Found. Unable to retrieve vehicle details from RTO databases."
            }
        )
        
    return JSONResponse(content={"status": "success", "data": merged_data})
