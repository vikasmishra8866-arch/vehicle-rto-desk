import os
import re
import asyncio
from typing import Dict, Any, List, Optional, Tuple, Set
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import httpx
from dotenv import load_dotenv

load_dotenv()

# Initialize FastAPI App & Rate Limiter
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Elite Vehicle RTO Desk")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

templates = Jinja2Templates(directory="templates")

# Configurable API Endpoints
API_URLS = [
    os.getenv("API_URL_1", "https://unsalubriously-unfragrant-rosetta.ngrok-free.dev/api/vehicle-details-only?regn_no={VEHICLE_NO}"),
    os.getenv("API_URL_2", "https://randkikichut.vercel.app/?vehicle_number={VEHICLE_NO}"),
    os.getenv("API_URL_3", "https://cjpen.vercel.app/vehicle/{VEHICLE_NO}"),
    os.getenv("API_URL_4", "https://cjpen.vercel.app/vehicle/{VEHICLE_NO}")
]

ALLOW_INSECURE_SSL = os.getenv("ALLOW_INSECURE_SSL", "False").lower() in ("true", "1", "yes")

# Explicit Mapping Engine
KEY_MAPPINGS: Dict[str, str] = {
    # 1. Vehicle Registration & Identity
    "regnno": "regn_no", "registrationnumber": "regn_no", "vehiclenumber": "regn_no", "regno": "regn_no", "rcnumber": "regn_no",
    "maker": "maker", "makername": "maker", "manufacturer": "maker", "makerdesc": "maker",
    "model": "model", "makermodel": "model", "modelname": "model",
    "variant": "variant", "vehiclevariant": "variant", "variantname": "variant",
    "fuel": "fuel_type", "fueltype": "fuel_type", "fueldesc": "fuel_type",
    "norms": "norms", "normstype": "norms", "emissionnorms": "norms", "vehancenorms": "norms", "vehiclenorms": "norms",

    # 2. Owner Details
    "ownername": "owner_name", "owner": "owner_name", "registeredownername": "owner_name", "ownernamevahan": "owner_name", "currentownername": "owner_name",
    "fathername": "father_name", "fatherhusbandname": "father_name", "fname": "father_name", "careof": "father_name",
    "ownerserial": "owner_serial", "ownersrno": "owner_serial", "ownercount": "owner_serial", "ownernumber": "owner_serial",
    "nominee": "nominee", "nomineename": "nominee",

    # 3. Address Pipeline
    "presentaddress": "addresses", "permanentaddress": "addresses", "address": "addresses", "owneraddress": "addresses", "fulladdress": "addresses", "currentaddress": "addresses",

    # 4. Technical Specifications
    "chassisno": "chassis_no", "chassisnumber": "chassis_no", "chassis": "chassis_no",
    "engineno": "engine_no", "enginenumber": "engine_no", "engine": "engine_no",
    "cubiccapacity": "engine_cc", "cc": "engine_cc", "enginecapacity": "engine_cc", "cubiccap": "engine_cc",
    "unladenweight": "unladen_weight", "unladenwt": "unladen_weight", "weight": "unladen_weight",
    "color": "color", "vehiclecolor": "color", "colour": "color",
    "vehicleclass": "vehicle_class", "vhclass": "vehicle_class", "classdesc": "vehicle_class",

    # 5. RC Compliance & Dates
    "rto": "rto_name", "rtoname": "rto_name", "registeringauthority": "rto_name", "staterto": "rto_name",
    "regndt": "reg_date", "registrationdate": "reg_date", "regdate": "reg_date", "rcregndt": "reg_date",
    "mfgdate": "mfg_date", "mfgdt": "mfg_date", "manufacturingdate": "mfg_date", "manufdate": "mfg_date", "mfgyr": "mfg_date", "mfgmonthyr": "mfg_date",
    "fitupto": "rc_expiry", "rcexpiry": "rc_expiry", "validupto": "rc_expiry", "rcvalidupto": "rc_expiry",
    "taxupto": "tax_upto", "taxvalidupto": "tax_upto", "taxexpiry": "tax_upto",

    # 6. Insurance & PUC
    "insurancecompany": "insurance_company", "insurancename": "insurance_company", "insurer": "insurance_company", "icname": "insurance_company",
    "insurancepolicyno": "policy_no", "policyno": "policy_no", "insuranceno": "policy_no",
    "insuranceupto": "insurance_expiry", "insuranceexpiry": "insurance_expiry", "policyexpiry": "insurance_expiry",
    "pucnumber": "puc_number", "pucno": "puc_number", "puccertno": "puc_number",
    "pucupto": "puc_expiry", "pucexpiry": "puc_expiry", "puccvalidupto": "puc_expiry",

    # 7. Finance
    "financer": "financer", "financername": "financer", "financedby": "financer", "bankname": "financer",
    "financed": "is_financed", "hypothecated": "is_financed", "hypothecationstatus": "is_financed"
}

STANDARD_KEYS: Set[str] = set(KEY_MAPPINGS.values())

def sanitize_key(key: str) -> str:
    """Converts key to lowercase and strips non-alphanumeric chars."""
    return re.sub(r'[^a-z0-9]', '', key.lower())

def flatten_and_normalize(data: Any) -> List[Tuple[str, Any]]:
    """Recursively flattens nested JSON payloads into key-value tuples."""
    items = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                items.extend(flatten_and_normalize(v))
            else:
                items.append((k, v))
    elif isinstance(data, list):
        for item in data:
            items.extend(flatten_and_normalize(item))
    return items

async def fetch_api_data(client: httpx.AsyncClient, url: str) -> Optional[Dict[str, Any]]:
    try:
        response = await client.get(url, timeout=6.0)
        if response.status_code == 200:
            res_json = response.json()
            return res_json if isinstance(res_json, dict) else {"data": res_json}
    except Exception:
        pass
    return None

def normalize_and_aggregate(raw_responses: List[Dict[str, Any]]) -> Dict[str, Any]:
    mapped_data: Dict[str, Set[str]] = {}
    additional_data: Dict[str, Set[str]] = {}
    addresses_with_owner_serial: List[Tuple[int, str]] = []

    for raw in raw_responses:
        flattened = flatten_and_normalize(raw)
        
        # Track serial for address resolution
        current_owner_serial = 1
        for k, v in flattened:
            if sanitize_key(k) == "ownerserial" and str(v).isdigit():
                current_owner_serial = int(v)

        for k, v in flattened:
            if v is None or str(v).strip() in ["", "null", "None", "N/A", "-"]:
                continue
            
            clean_str_val = str(v).strip()
            sanitized_k = sanitize_key(k)

            if sanitized_k in KEY_MAPPINGS:
                std_key = KEY_MAPPINGS[sanitized_k]
                if std_key == "addresses":
                    addresses_with_owner_serial.append((current_owner_serial, clean_str_val))
                else:
                    if std_key not in mapped_data:
                        mapped_data[std_key] = set()
                    mapped_data[std_key].add(clean_str_val)
            else:
                # Leftover key goes to Box 8
                display_k = k.replace('_', ' ').replace('-', ' ').title()
                if display_k not in additional_data:
                    additional_data[display_k] = set()
                additional_data[display_k].add(clean_str_val)

    if not mapped_data and not additional_data and not addresses_with_owner_serial:
        return {}

    # Process Address Pipeline
    main_address = ""
    all_addresses = []

    if addresses_with_owner_serial:
        # Sort by highest owner serial first, then by address string length descending
        addresses_with_owner_serial.sort(key=lambda x: (x[0], len(x[1])), reverse=True)
        main_address = addresses_with_owner_serial[0][1]
        
        # Collect distinct address strings
        seen_addr = set()
        for _, addr in addresses_with_owner_serial:
            if addr.lower() not in seen_addr:
                seen_addr.add(addr.lower())
                all_addresses.append(addr)

    # Convert sets back to structured display representation
    final_mapped = {k: " / ".join(sorted(v)) for k, v in mapped_data.items()}
    final_additional = {k: " / ".join(sorted(v)) for k, v in additional_data.items()}

    return {
        "mapped": final_mapped,
        "additional": final_additional,
        "main_address": main_address,
        "all_addresses": all_addresses
    }

@app.get("/", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"result": None, "vehicle_no": ""}
    )

@app.post("/search", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def search_vehicle(request: Request, vehicle_number: str = Form(...)):
    clean_vehicle_no = re.sub(r'[^A-Za-z0-9]', '', vehicle_number).upper()

    if not clean_vehicle_no:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "error": "Please enter a valid vehicle registration number.",
                "vehicle_no": vehicle_number
            }
        )

    urls = [url.format(VEHICLE_NO=clean_vehicle_no) for url in API_URLS]

    async with httpx.AsyncClient(verify=not ALLOW_INSECURE_SSL) as client:
        tasks = [fetch_api_data(client, url) for url in urls]
        responses = await asyncio.gather(*tasks)

    valid_responses = [res for res in responses if res]
    aggregated_result = normalize_and_aggregate(valid_responses)

    if not valid_responses or not (aggregated_result.get("mapped") or aggregated_result.get("additional")):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "not_found": True,
                "vehicle_no": clean_vehicle_no
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "result": aggregated_result,
            "vehicle_no": clean_vehicle_no
        }
    )
