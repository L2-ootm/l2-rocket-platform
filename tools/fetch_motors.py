import urllib.request
import urllib.parse
import json
import os

MOTORS = ["20146N5800-P", "40960O8000-P", "9977M2245-P"]
OUT_DIR = "l2_engine/motors"
os.makedirs(OUT_DIR, exist_ok=True)

for m in MOTORS:
    # 1. Search for motor
    url = f"https://www.thrustcurve.org/api/v1/search.json?manufacturer=Cesaroni&designation={urllib.parse.quote(m)}&maxResults=1"
    req = urllib.request.Request(url, headers={'User-Agent': 'L2-OSIFOG'})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            if not data.get("results"):
                # fallback: just the suffix e.g. "O8000-P"
                fallback = m.split("0", 1)[1] if "0" in m else m
                url2 = f"https://www.thrustcurve.org/api/v1/search.json?manufacturer=Cesaroni&designation={urllib.parse.quote(fallback)}&maxResults=1"
                req_fallback = urllib.request.Request(url2, headers={'User-Agent': 'L2-OSIFOG'})
                with urllib.request.urlopen(req_fallback) as resp_fallback:
                    data2 = json.loads(resp_fallback.read().decode())
                    if not data2.get("results"):
                        print(f"Could not find {m}")
                        continue
                    motor_id = data2["results"][0]["motorId"]
            else:
                motor_id = data["results"][0]["motorId"]
            
        # 2. Get data files
        data_url = f"https://www.thrustcurve.org/api/v1/download.json?motorId={motor_id}"
        req2 = urllib.request.Request(data_url, headers={'User-Agent': 'L2-OSIFOG'})
        with urllib.request.urlopen(req2) as resp2:
            download_data = json.loads(resp2.read().decode())
            results = download_data.get("results", [])
            # find RASP format
            rasp_result = next((r for r in results if r.get("format") == "RASP"), None)
            if not rasp_result:
                print(f"No RASP format found for {m}")
                continue
            
            eng_data = rasp_result["data"]
            # save as the simple name (e.g. O8000.eng)
            simple_name = "O8000" if "O8000" in m else ("N5800" if "N5800" in m else "M2245")
            with open(os.path.join(OUT_DIR, f"{simple_name}.eng"), "w") as f:
                f.write(eng_data)
            print(f"Downloaded {simple_name}.eng")
    except Exception as e:
        print(f"Error for {m}: {e}")
