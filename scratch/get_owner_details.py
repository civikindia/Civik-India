import urllib.request
import json
import ssl

API_KEY = "rnd_fXA530LmlrcGiZrzmuCcRiRpH5uX"
SERVICE_ID = "srv-d8a4tdbeo5us739e774g"

def make_request(url, method="GET", body=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {API_KEY}")
    req.add_header("Accept", "application/json")
    
    context = ssl.create_default_context()
    
    try:
        with urllib.request.urlopen(req, context=context) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return 0, str(e)

def main():
    status, service = make_request(f"https://api.render.com/v1/services/{SERVICE_ID}")
    if status == 200:
        print("Owner ID:", service.get("ownerId") or service.get("owner", {}).get("id"))
        print("Region:", service.get("region"))
        print("Service Details:", json.dumps(service, indent=2))
    else:
        print("Error:", service)

if __name__ == "__main__":
    main()
