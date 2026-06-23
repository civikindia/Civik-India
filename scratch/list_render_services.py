import urllib.request
import json
import ssl

API_KEY = "rnd_fXA530LmlrcGiZrzmuCcRiRpH5uX"

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
    print("=== LISTING ALL RENDER SERVICES ===")
    status, services = make_request("https://api.render.com/v1/services?limit=20")
    print(f"Status code: {status}")
    if status == 200:
        for s in services:
            service = s.get('service', s)
            print(f"- Name: {service.get('name')}")
            print(f"  ID: {service.get('id')}")
            print(f"  Type: {service.get('type')}")
            print(f"  Status: {service.get('status')}")
            print(f"  Suspended: {service.get('suspended')}")
            print("-" * 30)
    else:
        print(f"Error: {services}")

if __name__ == "__main__":
    main()
