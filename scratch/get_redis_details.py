import urllib.request
import json
import ssl

API_KEY = "rnd_fXA530LmlrcGiZrzmuCcRiRpH5uX"
REDIS_ID = "red-d8di2rbeo5us73ev8nf0"

def make_request(url, method="GET", body=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {API_KEY}")
    req.add_header("Accept", "application/json")
    
    context = ssl.create_default_context()
    
    try:
        with urllib.request.urlopen(req, context=context) as response:
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
    print("=== GETTING KEY VALUE CONNECTION INFO ===")
    status, response = make_request(f"https://api.render.com/v1/key-value/{REDIS_ID}/connection-info")
    print(f"Status code: {status}")
    if status == 200:
        internal_conn = response.get("internalConnectionString")
        print("Internal Connection String (First 15 chars):", internal_conn[:15] + "..." if internal_conn else "None")
        print("Response Keys:", list(response.keys()))
        
        # Save connection details to a local JSON file in scratch directory
        with open("scratch/redis_connection_info.json", "w") as f:
            json.dump(response, f, indent=2)
        print("Successfully saved connection info to scratch/redis_connection_info.json")
    else:
        print("Response:", response)

if __name__ == "__main__":
    main()
