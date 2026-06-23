import urllib.request
import json
import ssl
import sys

API_KEY = "rnd_fXA530LmlrcGiZrzmuCcRiRpH5uX"
SERVICE_ID = "srv-d8a4tdbeo5us739e774g"

def make_request(url, method="PUT", body=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {API_KEY}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode("utf-8")
    else:
        data = None
    
    context = ssl.create_default_context()
    
    try:
        with urllib.request.urlopen(req, data=data, context=context) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
            return e.code, json.loads(err_body)
        except Exception:
            return e.code, str(e)
    except Exception as e:
        return 0, str(e)

def main():
    # 1. Read connection info
    try:
        with open("scratch/redis_connection_info.json", "r") as f:
            conn_info = json.load(f)
    except Exception as e:
        print("Error reading connection info file:", e)
        return
        
    internal_conn = conn_info.get("internalConnectionString")
    if not internal_conn:
        print("Error: No internalConnectionString found in file.")
        return
        
    print(f"Setting Redis environment variables on Render...")
    
    # We set:
    # REDIS_URL -> internal_conn
    # CELERY_BROKER_URL -> internal_conn + "/0"
    # CELERY_RESULT_BACKEND -> internal_conn + "/1"
    
    vars_to_set = {
        "REDIS_URL": internal_conn,
        "CELERY_BROKER_URL": f"{internal_conn}/0",
        "CELERY_RESULT_BACKEND": f"{internal_conn}/1"
    }
    
    for key, val in vars_to_set.items():
        url = f"https://api.render.com/v1/services/{SERVICE_ID}/env-vars/{key}"
        print(f"Setting {key}...")
        status, response = make_request(url, method="PUT", body={"value": val})
        print(f"  Status code: {status}")
        if status in [200, 201]:
            print(f"  Successfully set {key}!")
        else:
            print(f"  Failed to set {key}: {response}")
            
    print("Done setting variables!")

if __name__ == "__main__":
    main()
