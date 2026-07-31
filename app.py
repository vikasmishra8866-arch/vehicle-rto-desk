import os
import re
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

load_dotenv()

# Rate Limiter setup
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Vehicle RTO Data Aggregation Engine")
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

def clean_key(key: str) -> str:
    """Normalizes key strings for mapping comparisons."""
    return re.sub(r'[^a-zA-Z0-9]', '', str(key)).lower()

def safe_get(data: Any, keys: List[str], default=None) -> Any:
    """Recursively or dynamically searches for a key in dictionary payloads."""
    if not isinstance(data, dict):
        return default
    
    normalized_data = {clean_key(k): v for k, v in data.items()}
    for k in keys:
        norm_k = clean_key(k)
        if norm_k in normalized_data and normalized_data[norm_k] not in [None, "", "N/A", "null"]:
            val = normalized_data[norm_k]
            if isinstance(val, dict):
                return val
            return str(val).strip()
    return default

def extract_owner_serial(data: dict) -> int:
    val = safe_get(data, ["ownerSerial", "owner_serial", "ownerCount", "owner_number", "owner_seq"])
    if val:
        try:
            nums = re.findall(r'\d+', str(val))
            if nums:
                return int(nums[0])
        except Exception:
            pass
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

    # Track used raw key-value pairs across sources for catch-all extraction
    tracked_used_keys = set()
    
    # 1. Owner & Address Sorting Strategy
    owner_records = []
    for resp in valid_responses:
        # Check root or nested data dicts
        target = resp.get("data", resp) if isinstance(resp.get("data"), dict) else resp
        
        ser = extract_owner_serial(target)
        addr = safe_get(target, ["presentAddress", "permanentAddress", "address", "mainAddress", "ownerAddress", "fullAddress"])
        owner_records.append({
            "owner_serial": ser,
            "address": addr,
            "data": target
        })
    
    max_owner_serial = max([r["owner_serial"] for r in owner_records]) if owner_records else 1
    highest_owner_records = [r for r in owner_records if r["owner_serial"] == max_owner_serial]
    
    # Select longest address from highest owner serial
    main_address = None
    longest_len = -1
    for rec in highest_owner_records:
        if rec["address"]:
            if len(str(rec["address"])) > longest_len:
                longest_len = len(str(rec["address"]))
                main_address = str(rec["address"])
                
    # Collect all distinct addresses
    all_addresses = []
    for rec in owner_records:
        if rec["address"] and str(rec["address"]) not in all_addresses:
            all_addresses.append(str(rec["address"]))

    # Helper function for best value selection
    def pick_first(keys: List[str]) -> str:
        for rec in owner_records:
            val = safe_get(rec["data"], keys)
            if val is not None:
                return str(val)
        return "N/A"

    # Identity Details
    reg_no = vehicle_no.upper()
    maker = pick_first(["makerDescription", "maker", "manufacturer", "make", "maker_name"])
    model = pick_first(["makerModel", "model", "modelName", "vehicleModel"])
    variant = pick_first(["variant", "vehicleVariant", "subModel"])
    fuel_type = pick_first(["fuelType", "fuel", "fuel_desc"])
    emission_norms = pick_first(["normsType", "emissionNorms", "norms", "normsDesc"])

    # Owner & Financer Details
    owner_name = pick_first(["ownerName", "owner", "currentOwner", "registeredOwner"])
    father_name = pick_first(["fatherName", "fatherHusbandName", "husbandName"])
    nominee = pick_first(["nomineeName", "nominee", "nominee_details"])
    financer = pick_first(["financer", "hypothecationDetails", "bankName", "financedBy"])
    is_financed = "YES (FINANCED)" if financer != "N/A" and financer != "" else "NO / UNFINANCED"

    # Technical Specs
    chassis = pick_first(["chassisNumber", "chassisNo", "chassis"])
    engine = pick_first(["engineNumber", "engineNo", "engine"])
    cubic_capacity = pick_first(["cubicCapacity", "engineCC", "cc", "cubic_capacity"])
    unladen_weight = pick_first(["unladenWeight", "weight", "vehicleWeight"])
    color = pick_first(["vehicleColor", "color", "colour"])
    v_class = pick_first(["vehicleClass", "class", "vehicleType", "category"])

    # RC Compliance & Validity
    rto_name = pick_first(["registeringAuthority", "rto", "rtoName", "registeredAt"])
    reg_date = pick_first(["registrationDate", "regDate", "registeredDate"])
    mfg_date = pick_first(["manufacturingYear", "mfgYear", "manuMonthYr", "manufacturingDate"])
    rc_expiry = pick_first(["fitUpto", "rcValidUpto", "rcExpiryDate", "rcValidity"])
    tax_expiry = pick_first(["taxUpto", "taxValidUpto", "taxExpiry"])

    # Insurance & PUC
    insurance_co = pick_first(["insuranceCompany", "insuranceComp", "insuranceName", "insurer"])
    policy_no = pick_first(["insurancePolicyNo", "policyNumber", "policyNo"])
    insurance_upto = pick_first(["insuranceUpto", "insuranceValidUpto", "insuranceExpiry"])
    prev_ncb = pick_first(["prevNcb", "ncb", "noClaimBonus"])

    puc_no = pick_first(["pucNumber", "pucNo", "pucCertificateNo"])
    puc_upto = pick_first(["pucUpto", "pucValidUpto", "pucExpiry"])

    # Catch-all extraction: gather any extra key-value pairs not covered above
    mapped_keys_norm = {
        "regnno", "vehiclenumber", "registrationno", "makerdescription", "maker", "manufacturer",
        "makermodel", "model", "modelname", "variant", "fueltype", "fuel", "normstype", "emissionnorms",
        "ownername", "owner", "fathername", "fatherhusbandname", "ownerserial", "nomineename", "nominee",
        "financer", "hypothecationdetails", "chassisnumber", "chassisno", "enginenumber", "engineno",
        "cubiccapacity", "enginecc", "unladenweight", "vehiclecolor", "color", "vehicleclass", "class",
        "registeringauthority", "rto", "rtoname", "registrationdate", "regdate", "manufacturingyear",
        "mfgyear", "fitupto", "rcvalidupto", "taxupto", "insurancecompany", "insurancepolicyno",
        "insuranceupto", "prevncb", "pucnumber", "pucupto", "presentaddress", "permanentaddress", "address",
        "mainaddress", "owneraddress", "fulladdress", "data", "status", "success", "message"
    }

    additional_specs = {}

    def extract_extra_fields(obj, prefix=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                norm_k = clean_key(k)
                if norm_k not in mapped_keys_norm:
                    if isinstance(v, (dict, list)):
                        extract_extra_fields(v, prefix=f"{k} ")
                    elif v not in [None, "", "N/A", "null"]:
                        formatted_key = f"{prefix}{k}".replace("_", " ").title()
                        additional_specs[formatted_key] = str(v)

    for resp in valid_responses:
        extract_extra_fields(resp)

    # Final Consolidated Master JSON Payload
    master_payload = {
        "primary_identity": {
            "vehicle_number": reg_no,
            "maker": maker,
            "model": model,
            "variant": variant,
            "fuel_type": fuel_type,
            "emission_norms": emission_norms
        },
        "owner_details": {
            "owner_name": owner_name,
            "father_husband_name": father_name,
            "owner_serial": str(max_owner_serial),
            "nominee": nominee
        },
        "address_details": {
            "main_address": main_address or "N/A",
            "all_addresses": all_addresses if len(all_addresses) > 1 else []
        },
        "technical_specs": {
            "chassis_number": chassis,
            "engine_number": engine,
            "cubic_capacity": cubic_capacity,
            "unladen_weight": unladen_weight,
            "vehicle_color": color,
            "vehicle_class": v_class
        },
        "financed_status": {
            "is_financed": is_financed,
            "financer_name": financer
        },
        "rc_compliance": {
            "rto_name": rto_name,
            "registration_date": reg_date,
            "mfg_date": mfg_date,
            "rc_expiry": rc_expiry,
            "tax_expiry": tax_expiry
        },
        "puc_details": {
            "puc_number": puc_no,
            "puc_expiry": puc_upto
        },
        "insurance_details": {
            "company_name": insurance_co,
            "policy_number": policy_no,
            "policy_expiry": insurance_upto,
            "prev_ncb": prev_ncb
        },
        "additional_specs": additional_specs
    }

    return master_payload

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
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
                "message": "Details Not Found. Unable to retrieve vehicle information from RTO databases."
            }
        )
        
    return JSONResponse(content={"status": "success", "data": merged_data})
