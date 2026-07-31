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

# Initialize Rate Limiter: 5 requests per minute per IP
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Elite Vehicle Desk Engine", version="2.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

templates = Jinja2Templates(directory="templates")

# Fallback RTO API Endpoints
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

def normalize_key(key: str) -> str:
    """Converts camelCase, PascalCase, and delimited string keys to clean snake_case."""
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', str(key))
    s2 = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()
    return re.sub(r'[^a-z0-9_]', '', s2).strip('_')

# Canonical Alias Mapping Table for Categories 1 to 7
ALIAS_MAP = {
    "regn_no": ["regn_no", "registration_number", "vehicle_number", "reg_no", "rc_number", "vehicleno"],
    "maker": ["maker", "maker_name", "manufacturer", "maker_desc", "maker_description", "make"],
    "model": ["model", "maker_model", "model_name", "vehicle_model"],
    "variant": ["variant", "vehicle_variant", "variant_name", "sub_model"],
    "fuel_type": ["fuel", "fuel_type", "fuel_desc", "fuel_type_descr"],
    "norms": ["norms", "norms_type", "emission_norms", "vehicle_norms", "norms_desc"],
    "owner_name": ["owner_name", "owner", "registered_owner_name", "owner_name_vahan", "current_owner_name", "current_owner"],
    "father_name": ["father_name", "father_husband_name", "f_name", "care_of", "fathername", "husband_name"],
    "owner_serial": ["owner_serial", "owner_sr_no", "owner_count", "owner_number", "owner_seq"],
    "nominee": ["nominee", "nominee_name", "nominee_details"],
    "addresses": ["present_address", "permanent_address", "address", "owner_address", "full_address", "current_address", "main_address"],
    "chassis_no": ["chassis_no", "chassis_number", "chassis"],
    "engine_no": ["engine_no", "engine_number", "engine"],
    "engine_cc": ["cubic_capacity", "cc", "engine_capacity", "cubic_cap", "engine_cc"],
    "unladen_weight": ["unladen_weight", "unladen_wt", "weight", "vehicle_weight"],
    "color": ["color", "vehicle_color", "colour"],
    "vehicle_class": ["vehicle_class", "vh_class", "class_desc", "class", "category"],
    "financer_name": ["financer", "financer_name", "hypothecation_details", "bank_name", "financed_by"],
    "rto_name": ["rto", "rto_name", "registering_authority", "state_rto", "registered_at"],
    "reg_date": ["regn_dt", "registration_date", "reg_date", "rc_regn_dt", "registered_date"],
    "mfg_date": ["mfg_date", "mfg_dt", "manufacturing_date", "manuf_date", "mfg_yr", "mfg_month_yr", "manufacturing_year"],
    "rc_expiry": ["fit_upto", "rc_expiry", "valid_upto", "rc_valid_upto", "rc_expiry_date", "rc_validity"],
    "tax_upto": ["tax_upto", "tax_valid_upto", "tax_expiry"],
    "insurance_company": ["insurance_company", "insurance_name", "insurer", "ic_name", "insurance_co", "insurance_comp"],
    "policy_no": ["insurance_policy_no", "policy_no", "insurance_no", "policy_number"],
    "insurance_expiry": ["insurance_upto", "insurance_expiry", "policy_expiry", "insurance_valid_upto"],
    "puc_number": ["puc_number", "puc_no", "puc_cert_no", "pucc_no", "puc_certificate_no"],
    "puc_expiry": ["puc_upto", "puc_expiry", "pucc_valid_upto", "puc_valid_upto"]
}

# Aggregate all mapped normalized keys to isolate extra fields into Box 8
ALL_MAPPED_NORM_KEYS = set()
for aliases in ALIAS_MAP.values():
    for alias in aliases:
        ALL_MAPPED_NORM_KEYS.add(normalize_key(alias))

def extract_flat_dict(raw_json: Any, parent_key: str = '') -> Dict[str, Any]:
    """Recursively flattens deeply nested JSON structures into single-level dicts."""
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
    """Extracts non-null field candidates matching mapped aliases and selects the richest value."""
    aliases = ALIAS_MAP.get(field_key, [])
    candidates = []

    for flat_dict in flat_responses:
        for orig_key, val in flat_dict.items():
            norm_k = normalize_key(orig_key)
            if any(norm_k == normalize_key(alias) or norm_k.endswith(f"_{normalize_key(alias)}") for alias in aliases):
                if val not in [None, "", "N/A", "null", "None", "-", "undefined"]:
                    candidates.append(str(val).strip())

    if not candidates:
        return "N/A"

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

    # Owner Serial & Longest-String Address Aggregation
    owner_records = []
    for flat_dict in flat_responses:
        owner_ser_val = None
        for k, v in flat_dict.items():
            if normalize_key(k) in [normalize_key(a) for a in ALIAS_MAP["owner_serial"]]:
                owner_ser_val = v
                break
        
        addr_val = None
        for k, v in flat_dict.items():
            if normalize_key(k) in [normalize_key(a) for a in ALIAS_MAP["addresses"]]:
                if v not in [None, "", "N/A", "null", "None", "-"]:
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

    # Determine Finance Status
    financer = extract_field_value(flat_responses, "financer_name")
    is_financed = "YES (FINANCED)" if financer != "N/A" and financer != "" else "NO / UNFINANCED"

    # Box 8 Isolation Strategy: Non-Standard Unmapped Attributes Only
    additional_specs = {}
    for flat_dict in flat_responses:
        for orig_key, val in flat_dict.items():
            norm_k = normalize_key(orig_key)
            
            is_mapped = False
            for mapped_key in ALL_MAPPED_NORM_KEYS:
                if norm_k == mapped_key or norm_k.endswith(f"_{mapped_key}"):
                    is_mapped = True
                    break

            if not is_mapped and val not in [None, "", "N/A", "null", "None", "-", "undefined"]:
                formatted_label = orig_key.replace("_", " ").replace(".", " ").title()
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
            "emission_norms": extract_field_value(flat_responses, "norms")
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
            "chassis_number": extract_field_value(flat_responses, "chassis_no"),
            "engine_number": extract_field_value(flat_responses, "engine_no"),
            "cubic_capacity": extract_field_value(flat_responses, "engine_cc"),
            "unladen_weight": extract_field_value(flat_responses, "unladen_weight"),
            "vehicle_color": extract_field_value(flat_responses, "color"),
            "vehicle_class": extract_field_value(flat_responses, "vehicle_class")
        },
        "financed_status": {
            "is_financed": is_financed,
            "financer_name": financer
        },
        "rc_compliance": {
            "rto_name": extract_field_value(flat_responses, "rto_name"),
            "registration_date": extract_field_value(flat_responses, "reg_date"),
            "mfg_date": extract_field_value(flat_responses, "mfg_date"),
            "rc_expiry": extract_field_value(flat_responses, "rc_expiry"),
            "tax_expiry": extract_field_value(flat_responses, "tax_upto")
        },
        "insurance_puc_details": {
            "company_name": extract_field_value(flat_responses, "insurance_company"),
            "policy_number": extract_field_value(flat_responses, "policy_no"),
            "policy_expiry": extract_field_value(flat_responses, "insurance_expiry"),
            "puc_number": extract_field_value(flat_responses, "puc_number"),
            "puc_expiry": extract_field_value(flat_responses, "puc_expiry")
        },
        "additional_specs": additional_specs
    }

    return master_payload

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    # Fixed Starlette TemplateResponse Syntax for modern FastAPI
    return templates.TemplateResponse(request=request, name="index.html")

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
